"""Tool Curation Layer — thin import shim delegating to scratchpad.

All curation logic now lives in ``agent.runtime.scratchpad.ScratchpadManager``.
This module exists only to keep imports working across the codebase.
"""

from __future__ import annotations

from typing import Iterable

from agent.runtime.scratchpad import scratch as _scratch


# ── Delegation functions ──────────────────────────────────────────────────────

def curate(messages, specs, *, force_full: bool = False) -> list:
    """Delegate to scratchpad.curate_tools(). ``messages`` is ignored —
    the scratchpad already holds the intent from initialization.
    """
    return _scratch.curate_tools(list(specs or []), force_full=force_full)


def curated_escalation_set(specs: list) -> list:
    """Return the auto-expand tool set (REFACTOR_DEEP subset)."""
    _REFACTOR_DEEP_NAMES = frozenset({
        "read_file", "write_file", "edit_file", "multi_edit",
        "run_command", "list_files", "grep", "run_tests",
        "glob", "search_smart", "run_install", "todo_write",
        "self_critique", "lookup_symbol",
        "lookup_imports", "lookup_dependents",
        "checkpoint", "rollback_file", "verify_files",
        "write_file_begin", "write_file_chunk", "write_file_finalize",
    })
    keep = []
    for spec in (specs or []):
        name = (spec.get("function", {}) or {}).get("name", "")
        if name in _REFACTOR_DEEP_NAMES:
            keep.append(spec)
    return keep or (list(specs) if specs else [])[:25]


def select_domains(text: str) -> set:
    """Return domain keys triggered by the text. Delegates to scratchpad."""
    if not text:
        return set()
    try:
        from agent.runtime.scratchpad import _select_domains as _sd
        return _sd(text)
    except Exception:
        return set()


def curation_misses() -> int:
    return 0


def stats(messages, specs) -> dict:
    """Basic stat reporting (simplified — actual stats live in scratchpad)."""
    full = list(specs or [])
    curated = curate(messages, full)
    return {
        "tools_available": len(full),
        "tools_sent": len(curated),
        "reduction_pct": round(100 * (len(full) - len(curated)) / max(len(full), 1), 1),
        "domains": sorted(select_domains(_latest_user_text(messages))),
    }


def curation_enabled() -> bool:
    from agent.env import getenv_bool
    return getenv_bool("KRYTH_TOOL_CURATION", True)


_curation_miss = lambda: None  # no-op; kept for import compatibility


# ── Helpers ────────────────────────────────────────────────────────────────────

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


# Legacy alias — kept so existing imports  don't crash.
escalate = force_full = None
