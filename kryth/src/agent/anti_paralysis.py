"""Anti-paralysis engine — execution budget, search dedup, impl mode nudges.

Superseded responsibilities (now in agent.memory):
  - File-read dedup → DuplicateDetector + RepoMemory
  - Mission/strategy memory → EpisodicMemory + RepoMemory

Still enforced here:
  - Search duplicate detection (hard block on same query)
  - Execution budget (MAX_ANALYSIS_STEPS per complexity tier)
  - Implementation mode (lock to edit/test/validate only)
  - Single investigation (freeze root-cause search after first hit)
  - Implementation queue (batch tasks, analyze once, implement all)
  - No replanning (workers may not re-plan if mission plan exists)
  - Stop after success (halt once files written + tests pass)
  - Metrics (analysis_steps, impl_steps, ratio, timing)

All state is per-session and thread-safe.
"""
from __future__ import annotations


import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


# ── Configuration ─────────────────────────────────────────────────────────────

# Max analysis tool calls before forcing implementation (Phase 4)
# Raised from original values to accommodate read-project tasks without
# prematurely triggering implementation mode. list_files is excluded from
# analysis counting — directory listing is navigation, not investigation.
MAX_ANALYSIS_STEPS = {
    "simple":  12,
    "medium":  15,
    "complex": 20,
}

# Search budget — how many grep/glob calls before forcing summarization
MAX_SEARCH_CALLS = 5

# Tools that count as "analysis" (read/search) vs "implementation" (write/run)
# list_files intentionally excluded — listing files is navigation, not analysis.
_ANALYSIS_TOOLS: Set[str] = {
    "read_file", "glob", "grep", "search_code", "semantic_search",
    "fts_search", "ast_search", "search_smart",
}
_IMPL_TOOLS: Set[str] = {
    "write_file", "edit_file", "multi_edit", "run_command",
    "run_tests", "run_install",
}
# Tools that trigger "root cause found" (investigation complete)
_ROOT_CAUSE_SIGNALS: Set[str] = {
    "grep", "search_code", "semantic_search", "fts_search", "ast_search",
}

# Search tools tracked for budget enforcement
_SEARCH_TOOLS: Set[str] = {
    "grep", "glob", "search_code", "semantic_search",
    "fts_search", "ast_search", "search_smart",
    "lookup_symbol",
}


# ── Per-session state ─────────────────────────────────────────────────────────

@dataclass
class AntiParalysisState:
    """All anti-paralysis tracking for one session."""
    # Phase 2: implementation mode
    impl_mode: bool = False
    impl_mode_reason: str = ""

    # Phase 3: investigation frozen
    investigation_frozen: bool = False
    root_cause: str = ""

    # Phase 4: execution budget
    analysis_steps: int = 0
    impl_steps: int = 0
    budget_exhausted: bool = False

    # Phase 8: stop-after-success
    files_written: int = 0
    written_files: Dict[str, int] = field(default_factory=dict)  # path → count
    tests_passed: bool = False
    success_stop: bool = False

    # Phase 5: implementation queue
    impl_queue: List[str] = field(default_factory=list)
    impl_queue_analyzed: bool = False

    # V1.6: timing, search metrics & nudge tracking
    analysis_time_s: float = 0.0
    impl_time_s: float = 0.0
    search_count: int = 0
    duplicate_searches: int = 0
    _search_seen: Set[str] = field(default_factory=set)
    impl_nudge_count: int = 0
    search_nudge_sent: bool = False

    # Timing
    start_time: float = field(default_factory=time.monotonic)


_sessions: Dict[int, AntiParalysisState] = {}
_lock = threading.RLock()


def _state_for(session_id: int) -> AntiParalysisState:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = AntiParalysisState()
        return _sessions[session_id]


def reset_for_session(session_id: int) -> None:
    with _lock:
        _sessions.pop(session_id, None)


# ── Phase 2: Implementation Mode ──────────────────────────────────────────────

def enter_impl_mode(session_id: int, reason: str = "") -> None:
    """Switch this session into Implementation Mode."""
    st = _state_for(session_id)
    with _lock:
        st.impl_mode = True
        st.impl_mode_reason = reason or "root cause identified"


def is_impl_mode(session_id: int) -> bool:
    return _state_for(session_id).impl_mode


def impl_mode_nudge(reason: str = "") -> str:
    """Prompt text injected when in Implementation Mode."""
    return (
        "[IMPLEMENTATION MODE] Root cause identified. "
        "Rules:\n"
        "- NO repository scans\n"
        "- NO architecture reviews\n"
        "- NO redesign discussions\n"
        "Only: edit the specific files, update tests, validate.\n"
        f"Reason: {reason or 'root cause already found'}"
    )


# ── Phase 3: Single Investigation ─────────────────────────────────────────────

def freeze_investigation(session_id: int, root_cause: str) -> None:
    """Freeze further investigation once root cause is found."""
    st = _state_for(session_id)
    with _lock:
        st.investigation_frozen = True
        st.root_cause = root_cause
    enter_impl_mode(session_id, root_cause)


def is_investigation_frozen(session_id: int) -> bool:
    return _state_for(session_id).investigation_frozen


def get_root_cause(session_id: int) -> str:
    return _state_for(session_id).root_cause


# ── Phase 4: Execution Budget ──────────────────────────────────────────────────

def record_tool_call(session_id: int, tool_name: str, complexity: str = "medium", path: str = "") -> Optional[str]:
    """Record a tool call. Returns a nudge string if analysis budget is exhausted."""
    st = _state_for(session_id)
    with _lock:
        if tool_name in _ANALYSIS_TOOLS:
            st.analysis_steps += 1
        elif tool_name in _IMPL_TOOLS:
            st.impl_steps += 1
            if tool_name == "write_file" and path:
                if path in st.written_files:
                    # Same file written again — task is done, stop
                    st.success_stop = True
                    return (
                        f"[STOP] File '{path}' already written successfully. "
                        "Task complete — do not write the same file again."
                    )
                st.written_files.add(path)
                st.files_written += 1

        # Track search calls for budget enforcement
        if tool_name in _SEARCH_TOOLS:
            st.search_count += 1

        # Search budget: if too many searches with no implementation, nudge once
        if st.search_count > MAX_SEARCH_CALLS and not st.search_nudge_sent:
            st.search_nudge_sent = True
            return (
                f"[SEARCH BUDGET] Search limit ({MAX_SEARCH_CALLS} calls) exceeded "
                f"({st.search_count} searches). Answer with what you found — "
                "do NOT search further."
            )

        limit = MAX_ANALYSIS_STEPS.get(complexity, MAX_ANALYSIS_STEPS["medium"])
        if st.analysis_steps > limit and not st.impl_mode and not st.budget_exhausted:
            st.budget_exhausted = True
            enter_impl_mode(session_id, f"analysis budget ({limit} steps) exhausted")
            return (
                f"[EXECUTION BUDGET] Analysis limit ({limit} steps for {complexity} task) reached. "
                "Switch immediately to implementation. "
                "Call write_file or edit_file now — do NOT read or search further."
            )
    return None


def get_analysis_ratio(session_id: int) -> float:
    """Return analysis_steps / (analysis_steps + impl_steps). Goal < 0.3."""
    st = _state_for(session_id)
    with _lock:
        total = st.analysis_steps + st.impl_steps
        if total == 0:
            return 0.0
        return st.analysis_steps / total


# ── Phase 5: Implementation Queue ─────────────────────────────────────────────

def set_impl_queue(session_id: int, tasks: List[str]) -> None:
    """Set the implementation queue — tasks to execute after one analysis pass."""
    st = _state_for(session_id)
    with _lock:
        st.impl_queue = list(tasks)
        st.impl_queue_analyzed = False


def mark_queue_analyzed(session_id: int) -> None:
    st = _state_for(session_id)
    with _lock:
        st.impl_queue_analyzed = True


def get_impl_queue(session_id: int) -> List[str]:
    return list(_state_for(session_id).impl_queue)


def impl_queue_nudge(tasks: List[str]) -> str:
    """Prompt injected to implement all queued tasks without re-analysis."""
    task_list = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(tasks[:10]))
    return (
        "[IMPLEMENTATION QUEUE] Analysis complete. "
        "Implement ALL tasks below in sequence — no further analysis:\n"
        f"{task_list}\n"
        "For each task: edit files → update tests → verify. Then move to next."
    )


# ── Phase 6: No Replanning ─────────────────────────────────────────────────────

def worker_plan_guard(has_mission_plan: bool) -> Optional[str]:
    """Return a blocking message if a worker tries to replan when a plan exists."""
    if not has_mission_plan:
        return None
    return (
        "[NO REPLANNING] A mission plan already exists and has been approved. "
        "Do NOT re-plan, re-scope, or re-architect. "
        "Planner owns planning. You own execution. Implement your assigned tasks now."
    )


# ── Phase 8: Stop After Success ────────────────────────────────────────────────

def record_tests_passed(session_id: int) -> None:
    st = _state_for(session_id)
    with _lock:
        st.tests_passed = True


def should_stop(session_id: int) -> bool:
    """True when files have been written and tests passed — stop immediately."""
    st = _state_for(session_id)
    with _lock:
        return st.files_written > 0 and st.tests_passed


def stop_after_success_nudge() -> str:
    return (
        "[STOP AFTER SUCCESS] Files modified and tests passing. "
        "Task is COMPLETE. Do NOT re-audit, re-review, or re-investigate. "
        "Emit your final summary now."
    )


# ── Phase 9: Metrics ───────────────────────────────────────────────────────────

@dataclass
class ParalysisMetrics:
    duplicate_searches: int = 0
    analysis_steps: int = 0
    impl_steps: int = 0
    analysis_to_edit_ratio: float = 0.0
    elapsed_s: float = 0.0
    success: bool = False


def get_metrics(session_id: int) -> ParalysisMetrics:
    st = _state_for(session_id)
    with _lock:
        total = st.analysis_steps + st.impl_steps
        ratio = st.analysis_steps / total if total else 0.0
        return ParalysisMetrics(
            duplicate_searches=st.duplicate_searches,
            analysis_steps=st.analysis_steps,
            impl_steps=st.impl_steps,
            analysis_to_edit_ratio=ratio,
            elapsed_s=time.monotonic() - st.start_time,
            success=st.files_written > 0,
        )


def format_metrics(session_id: int) -> str:
    m = get_metrics(session_id)
    ratio_ok = "✓" if m.analysis_to_edit_ratio < 0.3 else "✗"
    return (
        f"  Analysis steps: {m.analysis_steps}  |  "
        f"Impl steps: {m.impl_steps}  |  "
        f"Ratio: {m.analysis_to_edit_ratio:.2f} {ratio_ok} (goal <0.3)  |  "
        f"Duplicate searches: {m.duplicate_searches}  |  "
        f"Elapsed: {m.elapsed_s:.1f}s"
    )


# ── V1.6 Phase 1: Timing & Search Tracking ────────────────────────────────────

def record_timing(session_id: int, tool_name: str, elapsed_s: float) -> None:
    """Accumulate per-tool timing into analysis vs implementation buckets."""
    if elapsed_s <= 0:
        return
    st = _state_for(session_id)
    with _lock:
        if tool_name in _ANALYSIS_TOOLS:
            st.analysis_time_s += elapsed_s
        elif tool_name in _IMPL_TOOLS:
            st.impl_time_s += elapsed_s


def record_search(session_id: int, query: str) -> bool:
    """Record a search. Returns True if this query is a duplicate."""
    if not query:
        return False
    key = query.strip().lower()[:120]
    st = _state_for(session_id)
    with _lock:
        st.search_count += 1
        if key in st._search_seen:
            st.duplicate_searches += 1
            return True
        st._search_seen.add(key)
    return False


# ── V1.6 Phase 3: Implementation mode nudge injection ─────────────────────────

def should_inject_impl_nudge(session_id: int, had_tool_calls: bool) -> bool:
    """True when in impl mode but model produced no tool calls (stalling).
    Limited to 2 injections to avoid spam.
    """
    if had_tool_calls:
        return False
    st = _state_for(session_id)
    with _lock:
        if not st.impl_mode:
            return False
        if st.impl_nudge_count >= 2:
            return False
        st.impl_nudge_count += 1
        return True
