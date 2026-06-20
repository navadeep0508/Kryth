"""Anti-paralysis engine — eliminate analysis loops in KRYTH.

KRYTH was observed:
  - Reading the same file multiple times
  - Re-analyzing already understood code
  - Re-discovering known bugs
  - Cycling through implement → audit → implement → audit
  - Performing repo-wide searches after target is known
  - Generating excessive reasoning before changes

This module enforces:
  Phase 1 — Read Budget:          track + deduplicate file reads
  Phase 2 — Implementation Mode:  lock to edit/test/validate only once root cause found
  Phase 3 — Single Investigation: freeze root-cause search after first hit
  Phase 4 — Execution Budget:     MAX_ANALYSIS_STEPS per complexity tier
  Phase 5 — Implementation Queue: batch remaining tasks, analyze once, implement all
  Phase 6 — No Replanning:        workers may not re-plan if a mission plan exists
  Phase 7 — Cached Knowledge:     per-session fact cache checked before read/search
  Phase 8 — Stop After Success:   once files written + tests pass, halt
  Phase 9 — Metrics:              track analysis_steps, impl_steps, duplicate_reads

All state is per-session and thread-safe.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


# ── Configuration ─────────────────────────────────────────────────────────────

# Max analysis tool calls before forcing implementation (Phase 4)
MAX_ANALYSIS_STEPS = {
    "simple":  3,
    "medium":  8,
    "complex": 15,
}

# Tools that count as "analysis" (read/search) vs "implementation" (write/run)
_ANALYSIS_TOOLS: Set[str] = {
    "read_file", "glob", "grep", "search_code", "semantic_search",
    "list_files", "fts_search", "ast_search", "search_smart",
}
_IMPL_TOOLS: Set[str] = {
    "write_file", "edit_file", "multi_edit", "run_command",
    "run_tests", "run_install",
}
# Tools that trigger "root cause found" (investigation complete)
_ROOT_CAUSE_SIGNALS: Set[str] = {
    "grep", "search_code", "semantic_search", "fts_search", "ast_search",
}


# ── Per-session state ─────────────────────────────────────────────────────────

@dataclass
class AntiParalysisState:
    """All anti-paralysis tracking for one session."""
    # Phase 1: read budget
    file_reads: Dict[str, int] = field(default_factory=dict)   # path → count
    duplicate_reads: int = 0

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

    # Phase 7: cached knowledge
    known_facts: Dict[str, str] = field(default_factory=dict)   # key → value
    files_inspected: Set[str] = field(default_factory=set)
    root_causes: List[str] = field(default_factory=list)
    completed_fixes: List[str] = field(default_factory=list)

    # Phase 8: stop-after-success
    files_written: int = 0
    tests_passed: bool = False
    success_stop: bool = False

    # Phase 5: implementation queue
    impl_queue: List[str] = field(default_factory=list)
    impl_queue_analyzed: bool = False

    # V1.6: timing & search metrics
    analysis_time_s: float = 0.0
    impl_time_s: float = 0.0
    search_count: int = 0
    duplicate_searches: int = 0
    _search_seen: Set[str] = field(default_factory=set)

    # V1.6: file summaries for mission memory
    file_summaries: Dict[str, str] = field(default_factory=dict)  # path → summary

    # V1.6: impl mode nudge counter (avoid spamming)
    impl_nudge_count: int = 0

    # Timing
    start_time: float = field(default_factory=time.monotonic)


_sessions: Dict[int, AntiParalysisState] = {}
_lock = threading.Lock()


def _state_for(session_id: int) -> AntiParalysisState:
    with _lock:
        if session_id not in _sessions:
            _sessions[session_id] = AntiParalysisState()
        return _sessions[session_id]


def reset_for_session(session_id: int) -> None:
    with _lock:
        _sessions.pop(session_id, None)


# ── Phase 1: Read Budget ───────────────────────────────────────────────────────

def record_file_read(session_id: int, path: str) -> bool:
    """Record a file read. Returns True if this is a duplicate (cached)."""
    if not path:
        return False
    st = _state_for(session_id)
    with _lock:
        count = st.file_reads.get(path, 0)
        st.file_reads[path] = count + 1
        st.files_inspected.add(path)
        if count > 0:
            st.duplicate_reads += 1
            return True   # duplicate — caller may short-circuit
    return False


def get_duplicate_read_warning(path: str) -> str:
    """Return a short warning message for a duplicate read."""
    return (
        f"[anti-paralysis] Avoided duplicate read: {path!r} "
        "— using cached understanding. Skip re-reading."
    )


def was_file_read(session_id: int, path: str) -> bool:
    st = _state_for(session_id)
    with _lock:
        return st.file_reads.get(path, 0) > 0


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
        st.root_causes.append(root_cause)
    enter_impl_mode(session_id, root_cause)


def is_investigation_frozen(session_id: int) -> bool:
    return _state_for(session_id).investigation_frozen


def get_root_cause(session_id: int) -> str:
    return _state_for(session_id).root_cause


# ── Phase 4: Execution Budget ──────────────────────────────────────────────────

def record_tool_call(session_id: int, tool_name: str, complexity: str = "medium") -> Optional[str]:
    """Record a tool call. Returns a nudge string if analysis budget is exhausted."""
    st = _state_for(session_id)
    with _lock:
        if tool_name in _ANALYSIS_TOOLS:
            st.analysis_steps += 1
        elif tool_name in _IMPL_TOOLS:
            st.impl_steps += 1
            if tool_name == "write_file":
                st.files_written += 1

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


# ── Phase 7: Cached Knowledge ──────────────────────────────────────────────────

def cache_fact(session_id: int, key: str, value: str) -> None:
    st = _state_for(session_id)
    with _lock:
        st.known_facts[key] = value


def get_cached_fact(session_id: int, key: str) -> Optional[str]:
    return _state_for(session_id).known_facts.get(key)


def record_completed_fix(session_id: int, description: str) -> None:
    st = _state_for(session_id)
    with _lock:
        st.completed_fixes.append(description)


def get_session_summary(session_id: int) -> str:
    """Return a compact summary of what's been learned/done this session."""
    st = _state_for(session_id)
    with _lock:
        parts = []
        if st.files_inspected:
            parts.append(f"Files read: {', '.join(list(st.files_inspected)[:8])}")
        if st.root_causes:
            parts.append(f"Root causes: {'; '.join(st.root_causes[:3])}")
        if st.completed_fixes:
            parts.append(f"Fixes applied: {'; '.join(st.completed_fixes[:5])}")
        if st.known_facts:
            facts = "; ".join(f"{k}={v[:40]}" for k, v in list(st.known_facts.items())[:4])
            parts.append(f"Known: {facts}")
    return "\n".join(parts) if parts else "(no cached knowledge yet)"


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
    duplicate_file_reads: int = 0
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
            duplicate_file_reads=st.duplicate_reads,
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
        f"Duplicate reads: {m.duplicate_file_reads}  |  "
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


# ── V1.6 Phase 1: Mission Report ──────────────────────────────────────────────

@dataclass
class MissionReport:
    """Before/after metrics comparison for a mission."""
    session_id: int
    total_file_reads: int = 0
    duplicate_file_reads: int = 0
    total_searches: int = 0
    duplicate_searches: int = 0
    analysis_steps: int = 0
    impl_steps: int = 0
    analysis_time_s: float = 0.0
    impl_time_s: float = 0.0
    analysis_to_edit_ratio: float = 0.0
    elapsed_s: float = 0.0
    files_written: int = 0
    tests_passed: bool = False


def generate_report(session_id: int) -> MissionReport:
    """Generate a MissionReport for the current session."""
    st = _state_for(session_id)
    with _lock:
        total = st.analysis_steps + st.impl_steps
        ratio = st.analysis_steps / total if total else 0.0
        return MissionReport(
            session_id=session_id,
            total_file_reads=sum(st.file_reads.values()),
            duplicate_file_reads=st.duplicate_reads,
            total_searches=st.search_count,
            duplicate_searches=st.duplicate_searches,
            analysis_steps=st.analysis_steps,
            impl_steps=st.impl_steps,
            analysis_time_s=st.analysis_time_s,
            impl_time_s=st.impl_time_s,
            analysis_to_edit_ratio=ratio,
            elapsed_s=time.monotonic() - st.start_time,
            files_written=st.files_written,
            tests_passed=st.tests_passed,
        )


def format_mission_report(session_id: int) -> str:
    """Format a human-readable mission performance report."""
    r = generate_report(session_id)
    lines = [
        "═══ KRYTH Mission Performance Report ═══",
        f"  File reads:          {r.total_file_reads:4d}  (dup: {r.duplicate_file_reads})",
        f"  Searches:            {r.total_searches:4d}  (dup: {r.duplicate_searches})",
        f"  Analysis steps:      {r.analysis_steps:4d}  ({r.analysis_time_s:.1f}s)",
        f"  Impl steps:          {r.impl_steps:4d}  ({r.impl_time_s:.1f}s)",
        f"  Analysis/edit ratio: {r.analysis_to_edit_ratio:.2f}  {'✓ (< 0.3)' if r.analysis_to_edit_ratio < 0.3 else '✗ (> 0.3)'}",
        f"  Files written:       {r.files_written}",
        f"  Tests passed:        {'yes' if r.tests_passed else 'no'}",
        f"  Mission duration:    {r.elapsed_s:.1f}s",
        "═══════════════════════════════════════",
    ]
    return "\n".join(lines)


# ── V1.6 Phase 2: Mission Memory ──────────────────────────────────────────────

class MissionMemory:
    """Per-session mission memory: files inspected, root causes, fixes done.

    Checked BEFORE any file read or search to avoid duplicate discovery.
    """

    def __init__(self, session_id: int) -> None:
        self._sid = session_id

    def remember_file(self, path: str, summary: str = "") -> None:
        """Record that a file has been read and understood."""
        st = _state_for(self._sid)
        with _lock:
            st.files_inspected.add(path)
            if summary:
                st.file_summaries[path] = summary[:400]

    def recall_file(self, path: str) -> Optional[str]:
        """Return cached summary for path, or None if not yet read."""
        st = _state_for(self._sid)
        with _lock:
            if path not in st.files_inspected:
                return None
            return st.file_summaries.get(path, "(already read — use cached understanding)")

    def should_skip_read(self, path: str) -> bool:
        """True if this file was already read this session."""
        return _state_for(self._sid).file_summaries.get(path) is not None or \
               path in _state_for(self._sid).files_inspected

    def remember_root_cause(self, cause: str) -> None:
        freeze_investigation(self._sid, cause)

    def get_root_causes(self) -> List[str]:
        return list(_state_for(self._sid).root_causes)

    def remember_fix(self, fix: str) -> None:
        record_completed_fix(self._sid, fix)

    def get_fixes(self) -> List[str]:
        return list(_state_for(self._sid).completed_fixes)

    def to_prompt_block(self) -> str:
        """Compact memory block for injection into agent prompts."""
        st = _state_for(self._sid)
        with _lock:
            parts = []
            if st.files_inspected:
                files = ", ".join(sorted(st.files_inspected)[:10])
                parts.append(f"Files already read: {files}")
            if st.root_causes:
                parts.append(f"Root cause(s) found: {'; '.join(st.root_causes[:3])}")
            if st.completed_fixes:
                parts.append(f"Fixes applied: {'; '.join(st.completed_fixes[:5])}")
            if st._search_seen:
                parts.append(f"Searches already done: {len(st._search_seen)}")
        if not parts:
            return ""
        return "[MISSION MEMORY — do NOT re-read or re-discover these]\n" + "\n".join(parts)


_memory_instances: Dict[int, MissionMemory] = {}
_memory_lock = threading.Lock()


def get_memory(session_id: int) -> MissionMemory:
    """Get or create MissionMemory for a session."""
    with _memory_lock:
        if session_id not in _memory_instances:
            _memory_instances[session_id] = MissionMemory(session_id)
        return _memory_instances[session_id]


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
