"""Pick which model to call for a given task.

Today KRYTH hard-codes a tiered split: ``MAIN_MODEL`` for the agent
loop, ``PLANNER_MODEL`` for planning / critique / diagnose, and
``SUMMARIZER_MODEL`` for transcript compaction. That covers most of the
"cheap vs capable" tradeoffs, but the main loop is stuck on
``MAIN_MODEL`` even for trivial continuations (tool-result echo,
single-paragraph confirmations) where a smaller model would be just as
good and a lot cheaper.

This module centralises the routing decision so it can evolve without
spraying ``if size < N: model = …`` across the codebase. It is purely a
function over hints — no I/O, no globals — so it stays testable.

Opt-in: ``ask_llm_stream`` consults this router only when
``KRYTH_AUTO_ROUTE`` is truthy. The default is "use MAIN_MODEL", so
behaviour is unchanged for users who don't opt in.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.env import getenv, getenv_bool, getenv_int


# Below this payload size the main loop's job is mechanically simple
# (tool-result acknowledgement, short follow-up). A smaller model is
# usually enough. Tuned for the typical FreeModel / NVIDIA gateway; can
# be overridden by KRYTH_ROUTE_SMALL_THRESHOLD.
_DEFAULT_SMALL_THRESHOLD_CHARS = 6000


def _configured_models() -> tuple[str, str, str]:
    """Return current main/planner/summarizer model names.

    Keep this dynamic so changes made by /config during a REPL session
    are visible to routing without restarting the process.
    """
    from agent import llm

    return (
        getenv("KRYTH_MAIN_MODEL", llm.MAIN_MODEL),
        getenv("KRYTH_PLANNER_MODEL", llm.PLANNER_MODEL),
        getenv("KRYTH_SUMMARIZER_MODEL", llm.SUMMARIZER_MODEL),
    )


def __getattr__(name: str) -> str:
    """Backwards-compatible dynamic access for old constant imports."""
    main_model, planner_model, summarizer_model = _configured_models()
    if name == "MAIN_MODEL":
        return main_model
    if name == "PLANNER_MODEL":
        return planner_model
    if name == "SUMMARIZER_MODEL":
        return summarizer_model
    raise AttributeError(name)


def _small_threshold() -> int:
    return max(0, getenv_int("KRYTH_ROUTE_SMALL_THRESHOLD", _DEFAULT_SMALL_THRESHOLD_CHARS))


def _auto_route_enabled() -> bool:
    return getenv_bool("KRYTH_AUTO_ROUTE")


@dataclass
class RouteHints:
    """Inputs the router considers when picking a model.

    Keep this dataclass cheap — anything expensive to compute should NOT
    live here, because every turn instantiates one.
    """

    payload_chars: int = 0
    recent_failures: int = 0   # consecutive failed tool turns
    has_tool_specs: bool = True
    explicit_override: str | None = None


def pick_main_model(hints: RouteHints | None = None) -> str:
    """Pick the model for ``ask_llm_stream`` given the current hints.

    Rules (least → most permissive):
      * ``explicit_override`` wins unconditionally.
      * If ``KRYTH_AUTO_ROUTE`` is off, return ``MAIN_MODEL``.
      * If recent failures > 0, stay on MAIN_MODEL — a struggling turn
        is the worst time to drop capability.
      * If payload is small AND tool-call surface is light (no tools, or
        a short prompt without tool_specs), use PLANNER_MODEL.
      * Otherwise MAIN_MODEL.
    """
    h = hints or RouteHints()
    main_model, planner_model, _ = _configured_models()

    if h.explicit_override:
        return h.explicit_override

    if not _auto_route_enabled():
        return main_model

    if h.recent_failures > 0:
        return main_model

    threshold = _small_threshold()
    if h.payload_chars and h.payload_chars <= threshold:
        return planner_model

    return main_model


def pick_helper_model(kind: str) -> str:
    """Pick a model for the non-main helper calls.

    ``kind`` is one of ``planner / critique / diagnose / summarizer``.
    Unknown kinds fall back to PLANNER_MODEL — the safer default.
    """
    _, planner_model, summarizer_model = _configured_models()

    if kind in ("summarizer", "summarize", "compact"):
        return summarizer_model
    if kind in ("planner", "plan", "critique", "diagnose"):
        return planner_model
    return planner_model


def describe_routing() -> dict:
    """Diagnostic snapshot — surfaced by /diag so users can verify what
    routing is in effect."""
    main_model, planner_model, summarizer_model = _configured_models()
    return {
        "auto_route": _auto_route_enabled(),
        "small_threshold_chars": _small_threshold(),
        "main_model": main_model,
        "planner_model": planner_model,
        "summarizer_model": summarizer_model,
    }


__all__ = [
    "RouteHints",
    "MAIN_MODEL",
    "PLANNER_MODEL",
    "SUMMARIZER_MODEL",
    "pick_main_model",
    "pick_helper_model",
    "describe_routing",
]
