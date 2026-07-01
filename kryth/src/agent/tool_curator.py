"""Tool Curation Layer — token-efficient per-task tool selection.

Problem: sending all ~112 tool schemas (~13,400 tokens) on every model call is
wasteful. This layer sends only the tools relevant to the current task.

Architecture — intent groups (Claude-Code-style named sets):

  MINIMAL   (6 tools,  ~493 tok) — single-file create/run
  FIX       (8 tools,  ~700 tok) — bug fix; grep + test
  SEARCH    (6 tools,  ~500 tok) — pure codebase search
  MODIFY    (7 tools,  ~580 tok) — file modification (write/edit/delete)
  BUILD     (12 tools, ~1.1k tok) — multi-file build; needs install + todo
  REFACTOR  (12 tools, ~1.1k tok) — standard audit/refactor
  REFACTOR_DEEP (22 tools, ~2.0k tok) — comprehensive audit; extended set
  DOMAIN extras — browser/git/agents/etc added by keyword triggers

Hard caps per intent (enforced after curation):
  READ:       8 tools max
  SEARCH:    12 tools max
  MODIFY:    10 tools max  (via _MODIFY_CORE specialization)
  RUN:       15 tools max
  EXPLORE:   15 tools max
  EDGE:      20 tools max
  (no cap):  all tools permitted

Auto-expand safety (never escalates to full 112):
  Instead of switching to all 112 tool specs when the model needs a tool
  not in the curated set, the agent loop escalates to REFACTOR_DEEP (22
  tools) — a superset that covers read/write/search/run but avoids the
  28K-token overhead of the full browser/factory/mission/supervisor specs.

Contract (safety first):
- Curation only SHRINKS the offered tool set; it never changes how a tool runs.
- AUTO-EXPAND: if the model needs a tool that wasn't offered, the agent loop
  escalates to the mid-size REFACTOR_DEEP set (18 tools). Curation can never
  permanently break execution — worst case is one extra turn.
- Feature-flagged: KRYTH_TOOL_CURATION (default ON). Set to 0 to disable.

Target: avg ≤10 tools/call across all intent classes.
"""

from __future__ import annotations

import re
from typing import Iterable

from agent.env import getenv_bool


# ── Named intent groups ────────────────────────────────────────────────────────
# Target: avg ≤10 tools/call.  Each group is additive (each ⊇ previous).

# MINIMAL — single-file create / trivial run. 6 tools, ~493 tok.
_MINIMAL_CORE = frozenset({
    "read_file", "write_file", "edit_file", "multi_edit",
    "run_command", "list_files",
})

# FIX — bug fix, error investigation. 8 tools, ~700 tok.
# Tight set: find the bug (grep), edit the fix, verify (run_tests).
_FIX_CORE = _MINIMAL_CORE | frozenset({
    "grep",
    "run_tests",
})

# SEARCH — pure codebase exploration. 6 tools, ~500 tok.
# No write tools — search-only intent. search_smart is the meta-search
# that covers grep/glob/semantic; no need for individual search_code.
_SEARCH_CORE = frozenset({
    "read_file", "list_files",
    "grep", "glob", "search_smart", "lookup_symbol",
})

# MODIFY — file modification (write, edit, delete). 7 tools, ~580 tok.
# Strictly limited to core file operations + search to locate targets.
# No install/todo/critique — pure modify intent.
_MODIFY_CORE = _MINIMAL_CORE | frozenset({
    "grep", "delete_file",
})

# BUILD — multi-file build (API, app, lib). 12 tools, ~1.1k tok.
_BUILD_CORE = _FIX_CORE | frozenset({
    "glob", "search_smart",
    "run_install", "todo_write",
})

# REFACTOR — standard audit/refactor. 12 tools, ~1.1k tok.
# Uses search_smart as unified search (covers grep+glob+semantic).
_REFACTOR_CORE = _BUILD_CORE | frozenset({
    "self_critique", "lookup_symbol",
})

# REFACTOR_DEEP — comprehensive audit / codebase-wide migration. 18 tools.
# Only triggered by explicit "entire codebase / all files / comprehensive" language.
# This is also the auto-expand set (instead of all 112).
_REFACTOR_DEEP = _REFACTOR_CORE | frozenset({
    "lookup_imports", "lookup_dependents",
    "checkpoint", "rollback_file", "verify_files",
    "write_file_begin", "write_file_chunk", "write_file_finalize",
})

# Always-core fallback if no specific intent detected
_CORE_NAMES = _BUILD_CORE

# Extended core (legacy alias — maps to REFACTOR_DEEP)
_EXTENDED_CORE = _REFACTOR_DEEP


# ── Intent detection ──────────────────────────────────────────────────────────

# Single-file create (minimal):
# "create hello.py", "write a function", "make config.yaml"
# NOTE: "fix auth.py" is single-file BUT bug-fix needs search tools.
# Only match trivial creates and renames, not bug-fixes.
_TRIVIAL_CREATE_RE = re.compile(
    r"^(create|write|make|add)\s+\w.*\.(py|js|ts|html|css|sh|txt|md|json|yaml|yml)\b"
    r"|^(create|write|make)\s+(a\s+)?(file|script|function|class|hello|simple)\b"
    r"|\bprint\b.*\brun\b|\brun\b.*\bprint\b",
    re.I,
)

# Bug-fix / investigation intent: needs FIX group (search + diagnostics)
_FIX_RE = re.compile(
    r"\b(fix|bug|error|crash|debug|issue|problem|broken|failing|exception|traceback"
    r"|why\s+(is|does|doesn'?t|isn'?t|won'?t)|not\s+work|doesn'?t\s+work)\b",
    re.I,
)

# Project-level read intent: "read this project", "read the repo", etc.
# Needs list_files to discover structure before reading individual files.
_READ_PROJECT_RE = re.compile(
    r"^(read|open|show\s+me)\s+(the\s+|this\s+)?"
    r"(project|directory|folder|repo|codebase|files|structure)\b",
    re.I,
)

# Read-only intent: "read X", "show me X", "open X"
# Prevents list_files / glob / grep from being offered when user clearly
# wants to read a specific file.  Only read_file + lookup permitted.
_READ_RE = re.compile(
    r"^(read|open|cat|show\s+me)\s+\S+"
    r"|\bread\s+(the\s+)?(contents\s+of|content\s+of)\b",
    re.I,
)

# Read-only core: single file read, no list/search/run tools.
_READ_ONLY_CORE = frozenset({
    "read_file",
    "lookup_symbol",
})

# Read-only project core: includes list_files for project-level exploration.
_READ_PROJECT_CORE = frozenset({
    "read_file",
    "list_files",
    "lookup_symbol",
})

# Search / exploration intent
_SEARCH_RE = re.compile(
    r"\b(search|find|look\s+(for|through|at)|where\s+is|grep|locate|show\s+me"
    r"|what\s+(files|functions|classes)|list\s+all|scan|explore|navigate"
    r"|which\s+(file|module)|look\s+across\s+all)\b",
    re.I,
)

# Modify intent — file modification (write/edit/delete), not new build
_MODIFY_RE = re.compile(
    r"\b(modify|update|change|edit|rewrite|overwrite|append|prepend|insert|"
    r"remove\s+(line|function|import|code)|replace|rename|"
    r"write\s+(a\s+)?(file|script|function|class|hello|simple))\b",
    re.I,
)

# Run / execute intent — running commands, tests, scripts
_RUN_RE = re.compile(
    r"^(run|execute|start|launch)\s"
    r"|\b(run\s+(the\s+)?(test|suite|lint|format|build|dev|server|script|command|task))"
    r"|\b(execute\s+(this|that|the))"
    r"|\b(start\s+(the\s+)?(server|app|service))"
    r"|^(install|npm\s+(run|install|start|test)|pip\s+install|yarn)\b",
    re.I,
)

# Build / implement intent
_BUILD_RE = re.compile(
    r"\b(build|implement|create\s+(a|the|an)\s+(app|api|server|service|endpoint"
    r"|backend|frontend|cli|bot|module|package|project|system)"
    r"|add\s+(feature|support|endpoint|route|page|component)|set\s+up|scaffold"
    r"|install|package\.json|requirements\.txt)\b",
    re.I,
)

# Refactor / audit intent — standard REFACTOR group
_REFACTOR_RE = re.compile(
    r"\b(refactor|audit|analyze|optimiz|restructure|clean\s+up|improve"
    r"|review|all\s+files|across\s+the\s+codebase|throughout|everywhere)\b",
    re.I,
)

# Comprehensive / deep refactor — triggers REFACTOR_DEEP (more tools)
_REFACTOR_DEEP_RE = re.compile(
    r"\b(entire\s+codebase|comprehensive\s+(audit|review|refactor|analysis)"
    r"|audit\s+entire|refactor\s+everything|migrate\s+(entire|all)"
    r"|system.wide|codebase.wide|all\s+modules|every\s+file)\b",
    re.I,
)


# ── Domain classification ──────────────────────────────────────────────────────

# Essential browser tools — for "browse / scrape / research" tasks (6 tools).
# browser_use_task orchestrates everything; others are for focused extraction.
# Advanced tools (browser_state, screenshot, fill_form, tab, iframe) added only
# when explicitly requested (full browser mode via _BROWSER_FULL_RE).
_BROWSER_ESSENTIAL = frozenset({
    "browser_use_task",          # high-level orchestrator (covers 80% of use cases)
    "open_url",                  # navigate to specific URL
    "extract_data",              # structured data extraction
    "browser_search",            # web search
    "save_research_finding",     # persist findings
    "get_research_report",       # retrieve accumulated findings
})
# Full browser domain matchers (all browser_ tools)
_BROWSER_FULL_RE = re.compile(
    r"\b(fill|form|login|upload|submit|click|type|scroll|eval|tab|iframe"
    r"|interact|automate|multi.?step|sequence|workflow|authenticate)\b", re.I
)

_DOMAIN_TOOLS: dict[str, object] = {
    "browser": lambda n: n.startswith("browser_") or n in {
        "open_url", "fill_form", "upload_file", "extract_data",
        "download_content", "save_research_finding", "get_research_report",
        "check_browser_errors", "browser_use_task",
    },
    "git":          lambda n: n == "git_op",
    "mission":      lambda n: n.startswith("mission_"),
    "supervisor":   lambda n: n.startswith("supervisor_") or n.startswith("ownership_") or n in {"run_supervised_mission", "budget_status"},
    "factory":      lambda n: n.startswith("factory_"),
    "retrieval":    lambda n: n in {"fts_search", "ast_search", "graphify_query"},
    "terminal_adv": lambda n: n in {"shell_state", "shell_plan", "shell_run_plan", "shell_build_test_loop", "terminal_memory_recall"},
    "agents":       lambda n: n in {"spawn_agent", "spawn_agents_parallel", "run_task_graph"},
}

_DOMAIN_TRIGGERS: dict[str, re.Pattern] = {
    "browser": re.compile(
        r"\b(browser|navigate|click|scrape|scraping|crawl|website|webpage|url|https?://"
        r"|youtube|google\.com|open\s+the?\s*(site|page|url|browser)|web\s+automation"
        r"|browser_use|fill\s+the?\s*form|download\s+(from|the)\s*(web|site|page))\b",
        re.I,
    ),
    "git": re.compile(
        r"\b(git|commit|branch|merge|rebase|stage|stash|push|pull|clone|checkout"
        r"|diff|version\s+control)\b",
        re.I,
    ),
    "mission": re.compile(
        r"\b(mission|queue|schedule|pause|resume|cancel|retry|long.running|multi.day|backlog)\b",
        re.I,
    ),
    "supervisor": re.compile(
        r"\b(supervis|budget|escalat|replan|recover|ownership|health\s+check|predict)\b",
        re.I,
    ),
    "factory": re.compile(
        r"\b(factory|sprint|architecture\s+audit|code\s+review|maintenance\s+scan|deploy)\b",
        re.I,
    ),
    "retrieval": re.compile(
        r"\b(full.text|fts|ast|abstract\s+syntax|knowledge\s+graph|graphify|advanced\s+search)\b",
        re.I,
    ),
    "terminal_adv": re.compile(
        r"\b(build.test\s+loop|shell\s+plan|terminal\s+memory|run\s+plan)\b",
        re.I,
    ),
    "agents": re.compile(
        r"\b(parallel|sub.?agent|spawn|team|delegate|task\s+graph|multi.agent|concurrent)\b",
        re.I,
    ),
}


# ── Miss counter ───────────────────────────────────────────────────────────────

_MISS_COUNT = [0]


def _curation_miss() -> None:
    _MISS_COUNT[0] += 1
    try:
        from agent.model import telemetry as _tel
        _tel.incr("curation_escalation")
    except Exception:
        pass


def curation_misses() -> int:
    return _MISS_COUNT[0]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tool_name(spec: dict) -> str:
    return (spec.get("function", {}) or {}).get("name", "") or ""


def curation_enabled() -> bool:
    return getenv_bool("KRYTH_TOOL_CURATION", True)


def _latest_user_text(messages: Iterable[dict]) -> str:
    texts = []
    for m in messages or []:
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                texts.append(" ".join(
                    p.get("text", "") for p in c if isinstance(p, dict)))
    return "\n".join(texts[-4:])


def select_domains(text: str) -> set:
    """Return domain keys triggered by the text."""
    return {d for d, rx in _DOMAIN_TRIGGERS.items() if rx.search(text or "")}


# ── Public intent detection ────────────────────────────────────────────────────

# ── Hard caps per intent ────────────────────────────────────────────────────
# Enforced after curation: if the curated tool list exceeds the cap, tools
# beyond the cap are dropped (in reverse-relevance order). These caps prevent
# tool-explosion even when domain triggers add many extra tools.
_INTENT_CAPS: dict[str, int] = {
    "read":      8,
    "search":   12,
    "modify":   10,
    "run":      15,
    "build":    15,
    "refactor": 18,
    "explore":  15,
    "default":  20,
}

_CORE_GROUP_NAMES: dict[frozenset, str] = {}

def _register_core(name: str, group: frozenset) -> None:
    _CORE_GROUP_NAMES[group] = name

_register_core("MINIMAL", _MINIMAL_CORE)
_register_core("FIX", _FIX_CORE)
_register_core("SEARCH", _SEARCH_CORE)
_register_core("MODIFY", _MODIFY_CORE)
_register_core("BUILD", _BUILD_CORE)
_register_core("REFACTOR", _REFACTOR_CORE)
_register_core("REFACTOR_DEEP", _REFACTOR_DEEP)


def _core_name(group: frozenset) -> str | None:
    return _CORE_GROUP_NAMES.get(group)


def _intent_cap(core_name: str | None, domains: set) -> int:
    """Return the hard cap for a given intent group name + active domains."""
    if domains:
        # Domain tasks (browser, git) get more tools since they have
        # domain-specific extras added after the core group.
        return _INTENT_CAPS.get("explore", 20)
    if core_name is None:
        return _INTENT_CAPS["default"]
    low = core_name.lower()
    if low == "minimal":
        return _INTENT_CAPS["read"]
    if low == "fix":
        return _INTENT_CAPS["modify"]
    if low == "modify":
        return _INTENT_CAPS["modify"]
    if low == "search":
        return _INTENT_CAPS["search"]
    if low == "build":
        return _INTENT_CAPS["build"]
    if low in ("refactor", "refactor_deep"):
        return _INTENT_CAPS["refactor"]
    return _INTENT_CAPS["default"]


def _enforce_cap(tools: list, cap: int, name_order: list) -> list:
    """If tools exceed cap, drop lowest-priority tools.

    ``name_order`` lists tool names in priority order (most important first).
    Tools not in ``name_order`` go to the back (lowest priority).
    """
    if len(tools) <= cap:
        return tools
    scored = []
    for i, spec in enumerate(tools):
        n = _tool_name(spec)
        priority = name_order.index(n) if n in name_order else 9999
        scored.append((priority, i, spec))
    scored.sort()
    return [s for _, _, s in scored[:cap]]


def _priority_order_for_core(core_name: str | None) -> list:
    """Return tool priority list for a core name (most important first)."""
    if core_name == "MINIMAL":
        return ["read_file", "write_file", "edit_file", "multi_edit", "run_command", "list_files"]
    if core_name == "FIX":
        return ["read_file", "edit_file", "multi_edit", "grep", "run_command", "run_tests", "write_file", "list_files"]
    if core_name == "MODIFY":
        return ["read_file", "write_file", "edit_file", "multi_edit", "run_command", "list_files", "grep", "delete_file"]
    if core_name == "SEARCH":
        return ["read_file", "search_smart", "grep", "glob", "lookup_symbol", "list_files"]
    if core_name == "BUILD":
        return ["read_file", "write_file", "edit_file", "multi_edit", "run_command", "run_tests", "list_files", "grep", "glob", "search_smart", "run_install", "todo_write"]
    if core_name == "REFACTOR":
        return ["read_file", "edit_file", "grep", "search_smart", "glob", "list_files", "run_command", "run_tests", "run_install", "write_file", "multi_edit", "self_critique", "lookup_symbol", "todo_write"]
    if core_name == "REFACTOR_DEEP":
        return ["read_file", "edit_file", "grep", "search_smart", "glob", "list_files", "run_command", "run_tests", "run_install", "write_file", "multi_edit", "self_critique", "lookup_symbol", "lookup_imports", "lookup_dependents", "todo_write", "checkpoint", "rollback_file", "verify_files", "write_file_begin", "write_file_chunk", "write_file_finalize"]
    return [
        "read_file", "write_file", "edit_file", "multi_edit", "run_command",
        "delete_file", "list_files", "grep", "glob", "search_smart",
        "lookup_symbol", "run_tests", "run_install", "todo_write", "git_op",
    ]


def _pick_intent_group(text: str, domains: set) -> frozenset:
    """Map text + domains to the best-fit named intent group.

    Priority order (tightest match wins):
      1. Domain-active (browser/git/agents) → MINIMAL + domain tools
      2. Trivial single-file create → MINIMAL
      3. Comprehensive codebase audit → REFACTOR_DEEP (18 tools)
      4. Standard refactor/audit → REFACTOR (12 tools)
      5. Project-level read → READ_PROJECT (3 tools)
      6. Read-only → READ_ONLY (2 tools)
      7. Search/explore → SEARCH (6 tools, pure search)
      8. Modify → MODIFY (7 tools, no install/todo)
      9. Run/execute → MINIMAL (6 tools, core run tools)
     10. Fix/bug → FIX (8 tools)
     11. Build/implement → BUILD (12 tools)
     12. Default → BUILD

    Target avg: ≤10 tools/call across typical scenarios.
    """
    # Domain-active: MINIMAL + domain extras carry the weight
    if domains:
        return _MINIMAL_CORE

    # Trivial create: "create hello.py", "write a function"
    if _TRIVIAL_CREATE_RE.search(text.strip()):
        return _MINIMAL_CORE

    # Comprehensive refactor: entire codebase / all modules
    if _REFACTOR_DEEP_RE.search(text):
        return _REFACTOR_DEEP

    # Standard refactor/audit
    if _REFACTOR_RE.search(text):
        return _REFACTOR_CORE

    # Project-level read: need list_files to discover structure
    if _READ_PROJECT_RE.search(text):
        return _READ_PROJECT_CORE

    # Read-only: user asked to read/open a specific file
    if _READ_RE.search(text):
        return _READ_ONLY_CORE

    # Search/explore: search-heavy, no write tools
    if _SEARCH_RE.search(text) and not _BUILD_RE.search(text) and not _MODIFY_RE.search(text):
        return _SEARCH_CORE

    # Modify: file modification, not new build
    if _MODIFY_RE.search(text):
        return _MODIFY_CORE

    # Run/execute: running commands, tests, scripts
    if _RUN_RE.search(text):
        return _MINIMAL_CORE

    # Bug fix: grep + run_tests is enough to locate and verify
    if _FIX_RE.search(text):
        return _FIX_CORE

    # Build: multi-file implementation
    if _BUILD_RE.search(text):
        return _BUILD_CORE

    # Default: BUILD is safe and well-rounded
    return _BUILD_CORE


def curated_escalation_set(specs: list) -> list:
    """Return the auto-expand tool set (REFACTOR_DEEP = ~22 tools) instead of
    the full 112-tool set. Used by the agent loop when the model needs a tool
    not in the curated set — guarantees capability without the 28K-token overhead
    of browser/factory/mission/supervisor specs.

    Never returns all 112 specs — caps at REFACTOR_DEEP + domain extras.
    """
    core = _REFACTOR_DEEP  # 22 tools: read/write/search/run/refactor
    keep = []
    for spec in specs:
        name = _tool_name(spec)
        if name in core:
            keep.append(spec)
    return keep or specs[:25]  # hard safety: never exceed 25


def curate(messages, specs, *, force_full: bool = False) -> list:
    """Return the relevant subset of ``specs`` for the current task.

    Intent-group selection (target ≤10 tools average):
      MINIMAL       (6)  — single-file create/run
      FIX           (8)  — bug fix; grep + run_tests
      MODIFY        (7)  — file modification (write/edit/delete)
      SEARCH        (6)  — code exploration; pure search
      BUILD         (12) — multi-file build
      REFACTOR      (12) — standard audit/refactor
      REFACTOR_DEEP (18) — comprehensive codebase audit

    Hard caps are enforced after curation:
      READ/SHALL: 8, MODIFY: 10, RUN: 15, EXPLORE: 15, default: 20

    ``force_full`` or a disabled flag returns the full set.
    """
    specs = list(specs or [])
    if force_full or not curation_enabled():
        return specs

    text    = _latest_user_text(messages)
    domains = select_domains(text)
    active_core = _pick_intent_group(text, domains)

    # Detect whether this is a basic browse/research vs advanced browser interaction
    _browser_full = "browser" in domains and _BROWSER_FULL_RE.search(text)

    # Streaming write tools added when large-file / long-file keywords detected
    _streaming_names = frozenset({"write_file_begin", "write_file_chunk", "write_file_finalize"})
    _needs_streaming = bool(re.search(
        r"\b(large\s+file|long\s+file|stream|chunk|>200\s+lines|big\s+file|write\s+large)\b",
        text, re.I,
    ))

    keep = []
    for spec in specs:
        name = _tool_name(spec)
        if name in active_core:
            keep.append(spec)
            continue

        matched_domain = None
        for dom, pred in _DOMAIN_TOOLS.items():
            try:
                if pred(name):  # type: ignore[call-arg]
                    matched_domain = dom
                    break
            except Exception:
                pass

        if matched_domain is None:
            # Streaming write tools: add when large-file keywords present
            if name in _streaming_names and _needs_streaming:
                keep.append(spec)
        elif matched_domain in domains:
            # Browser: essential only unless advanced interaction requested
            if matched_domain == "browser" and not _browser_full:
                if name in _BROWSER_ESSENTIAL:
                    keep.append(spec)
            else:
                keep.append(spec)

    # Apply hard cap: prevent >20 tools reaching the LLM
    group_name = _core_name(active_core)
    cap = _intent_cap(group_name, domains)
    name_order = _priority_order_for_core(group_name)
    if len(keep) > cap:
        keep = _enforce_cap(keep, cap, name_order)

    return keep or specs[:25]  # hard safety: max 25, never full 112


def stats(messages, specs) -> dict:
    """Profiler: report curation effect for one request (no side effects)."""
    import json
    full    = list(specs or [])
    curated = curate(messages, full)

    def toks(o):
        return len(json.dumps(o)) // 4

    full_t = toks(full)
    cur_t  = toks(curated)
    text   = _latest_user_text(messages)
    domains = select_domains(text)
    group  = _pick_intent_group(text, domains)
    return {
        "tools_available":        len(full),
        "tools_sent":             len(curated),
        "schema_tokens_full":     full_t,
        "schema_tokens_curated":  cur_t,
        "tokens_saved":           full_t - cur_t,
        "reduction_pct":          round(100 * (full_t - cur_t) / full_t, 1) if full_t else 0.0,
        "domains":                sorted(domains),
        "intent_group":           _group_name(group),
    }


def _group_name(group: frozenset) -> str:
    if group is _MINIMAL_CORE:   return "MINIMAL"
    if group is _FIX_CORE:       return "FIX"
    if group is _MODIFY_CORE:    return "MODIFY"
    if group is _SEARCH_CORE:    return "SEARCH"
    if group is _BUILD_CORE:     return "BUILD"
    if group is _REFACTOR_CORE:  return "REFACTOR"
    if group is _REFACTOR_DEEP:  return "REFACTOR_DEEP"
    return f"CUSTOM({len(group)})"


# Re-export for escalation path in agent_loop.py
escalate = force_full = None  # unused; kept for import compat
