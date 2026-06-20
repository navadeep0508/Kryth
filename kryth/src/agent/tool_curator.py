"""Tool Curation Layer — token-efficient per-task tool selection.

Problem: sending all ~112 tool schemas (~13,400 tokens) on every model call is
wasteful. This layer sends only the tools relevant to the current task.

Architecture — intent groups (Claude-Code-style named sets):

  MINIMAL   (6 tools,  ~493 tok) — single-file create/run
  FIX       (8 tools,  ~700 tok) — bug fix; grep + test
  SEARCH    (8 tools,  ~700 tok) — code exploration; pure search
  BUILD     (12 tools, ~1.1k tok) — multi-file build; needs install + todo
  REFACTOR  (14 tools, ~1.3k tok) — audit/refactor; search + critique
  REFACTOR_DEEP (22 tools, ~2.0k tok) — comprehensive audit; full extended set
  DOMAIN extras — browser/git/agents/etc added by keyword triggers

Contract (safety first):
- Curation only SHRINKS the offered tool set; it never changes how a tool runs.
- AUTO-EXPAND: if the model needs a tool that wasn't offered, the agent loop
  escalates to the full set (session-scoped latch). Curation can never
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

# SEARCH — pure codebase exploration. 8 tools, ~700 tok.
# No write tools — search-only intent.
_SEARCH_CORE = frozenset({
    "read_file", "list_files",
    "grep", "glob", "search_smart", "semantic_search",
    "search_code", "lookup_symbol",
})

# BUILD — multi-file build (API, app, lib). 12 tools, ~1.1k tok.
_BUILD_CORE = _FIX_CORE | frozenset({
    "glob", "search_smart",
    "run_install", "todo_write",
})

# REFACTOR — standard audit/refactor. 14 tools, ~1.3k tok.
_REFACTOR_CORE = _BUILD_CORE | frozenset({
    "semantic_search", "search_code", "self_critique", "lookup_symbol",
})

# REFACTOR_DEEP — comprehensive audit / codebase-wide migration. 22 tools.
# Only triggered by explicit "entire codebase / all files / comprehensive" language.
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

# Search / exploration intent
_SEARCH_RE = re.compile(
    r"\b(search|find|look\s+(for|through|at)|where\s+is|grep|locate|show\s+me"
    r"|what\s+(files|functions|classes)|list\s+all|scan|explore|navigate"
    r"|which\s+(file|module)|look\s+across\s+all)\b",
    re.I,
)

# Build / implement intent
_BUILD_RE = re.compile(
    r"\b(build|implement|create\s+(a|the|an)\s+(app|api|server|service|endpoint"
    r"|backend|frontend|cli|bot|script|module|package|project|system)"
    r"|add\s+(feature|support|endpoint|route|page|component)|set\s+up|scaffold"
    r"|install|npm\s+(run|install)|pip\s+install|package\.json|requirements\.txt)\b",
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

def _pick_intent_group(text: str, domains: set) -> frozenset:
    """Map text + domains to the best-fit named intent group.

    Priority order (tightest match wins):
      1. Domain-active (browser/git/agents) → MINIMAL + domain tools
      2. Trivial single-file create → MINIMAL
      3. Comprehensive codebase audit → REFACTOR_DEEP (22 tools)
      4. Standard refactor/audit → REFACTOR (14 tools)
      5. Search/explore → SEARCH (8 tools, pure search)
      6. Fix/bug → FIX (8 tools)
      7. Build/implement → BUILD (12 tools)
      8. Default → BUILD

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

    # Search/explore: search-heavy, no write tools
    if _SEARCH_RE.search(text) and not _BUILD_RE.search(text):
        return _SEARCH_CORE

    # Bug fix: grep + run_tests is enough to locate and verify
    if _FIX_RE.search(text):
        return _FIX_CORE

    # Build: multi-file implementation
    if _BUILD_RE.search(text):
        return _BUILD_CORE

    # Default: BUILD is safe and well-rounded
    return _BUILD_CORE


def curate(messages, specs, *, force_full: bool = False) -> list:
    """Return the relevant subset of ``specs`` for the current task.

    Intent-group selection (target ≤10 tools average):
      MINIMAL       (6)  — single-file create/run
      FIX           (8)  — bug fix; grep + run_tests
      SEARCH        (8)  — code exploration; pure search
      BUILD         (12) — multi-file build
      REFACTOR      (14) — standard audit/refactor
      REFACTOR_DEEP (22) — comprehensive codebase audit

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

    return keep or specs


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
    if group is _SEARCH_CORE:    return "SEARCH"
    if group is _BUILD_CORE:     return "BUILD"
    if group is _REFACTOR_CORE:  return "REFACTOR"
    if group is _REFACTOR_DEEP:  return "REFACTOR_DEEP"
    return f"CUSTOM({len(group)})"


# Re-export for escalation path in agent_loop.py
escalate = force_full = None  # unused; kept for import compat
