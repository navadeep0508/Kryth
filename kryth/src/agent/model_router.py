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

Task-role routing (always active):
  KRYTH_MODEL_PLANNING  → used for DAG gen, intent, team gen, routing
  KRYTH_MODEL_CODING    → used for agent main loops (write/edit files)
  KRYTH_MODEL_REASONING → used for debugging, architecture decisions
  KRYTH_MODEL_VISION    → used for browser screenshot analysis
  KRYTH_MODEL_SUMMARY   → used for context compression
  All fall back to MAIN_MODEL / PLANNER_MODEL if unset.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agent.env import getenv, getenv_bool, getenv_int


# ---------------------------------------------------------------------------
# Task-role model routing
# ---------------------------------------------------------------------------

class TaskRole(Enum):
    """Role of an LLM call — used to pick the best model for the job."""
    PLANNING  = "planning"    # classify, plan, route, generate DAG/team
    CODING    = "coding"      # write/edit source files
    REASONING = "reasoning"   # debug, architecture, security decisions
    VISION    = "vision"      # browser screenshots, OCR, image analysis
    SUMMARY   = "summary"     # compress context, summarize transcripts
    SEARCH    = "search"      # interpret search results, rank files


def pick_model_for_role(role: TaskRole) -> str:
    """Return the configured model for a task role.

    Reads ``KRYTH_MODEL_{ROLE}`` env vars. Falls back to PLANNER_MODEL
    for PLANNING/SEARCH/SUMMARY, MAIN_MODEL for everything else.
    """
    main_model, planner_model, summarizer_model = _configured_models()
    env_key = f"KRYTH_MODEL_{role.value.upper()}"
    override = getenv(env_key, "").strip()
    if override:
        return override

    # Sensible defaults when env vars aren't set
    if role in (TaskRole.PLANNING, TaskRole.SEARCH):
        return planner_model
    if role == TaskRole.SUMMARY:
        return summarizer_model
    # CODING, REASONING, VISION → main model (most capable by default)
    return main_model


# Below this payload size the main loop's job is mechanically simple
# (tool-result acknowledgement, short follow-up). A smaller model is
# usually enough. Tuned for the typical FreeModel / NVIDIA gateway; can
# be overridden by KRYTH_ROUTE_SMALL_THRESHOLD.
_DEFAULT_SMALL_THRESHOLD_CHARS = 6000


def _configured_models() -> tuple[str, str, str]:
    """Return current main/planner/summarizer model names.

    Reads from model_config when available (supports unified + specialized
    YAML config). Falls back to env vars / llm module constants so existing
    setups are unchanged.
    """
    try:
        from agent.model_config.router import get_model_name as _mc_model
        return (
            _mc_model("main"),
            _mc_model("planner"),
            _mc_model("summary"),
        )
    except Exception:
        pass

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
    task_complexity: str = ""  # "simple" → try KRYTH_FAST_MODEL if configured


def pick_main_model(hints: RouteHints | None = None) -> str:
    """Pick the model for ``ask_llm_stream`` given the current hints.

    Rules (least → most permissive):
      * ``explicit_override`` wins unconditionally.
      * If ``KRYTH_FAST_MODEL`` is set, use it for simple tasks.
      * Otherwise return MAIN_MODEL.
    """
    h = hints or RouteHints()
    main_model, planner_model, _ = _configured_models()

    if h.explicit_override:
        return h.explicit_override

    # Fast-path for trivial tasks: use KRYTH_FAST_MODEL if configured.
    # Example: KRYTH_FAST_MODEL=meta/llama-3.1-8b-instruct for quick creates.
    if h.task_complexity == "simple":
        fast_model = getenv("KRYTH_FAST_MODEL", "").strip()
        if fast_model:
            return fast_model

    return main_model


def pick_helper_model(kind: str) -> str:
    """Pick a model for the non-main helper calls.

    ``kind`` is one of ``planner / critique / diagnose / summarizer /
    router / formatter / readme / docs``.

    Routing philosophy (fast = PLANNER_MODEL, strong = MAIN_MODEL):
    - Fast tasks: routing, formatting, docs, README, summarising — use PLANNER_MODEL.
    - Strong tasks: planning, code generation, critique — use MAIN_MODEL if available.
    - Fallback: PLANNER_MODEL is always safe.
    """
    main_model, planner_model, summarizer_model = _configured_models()

    # Always-fast tasks (no code reasoning required)
    if kind in ("summarizer", "summarize", "compact",
                "router", "route", "intent",
                "formatter", "format",
                "readme", "docs", "documentation"):
        return summarizer_model

    # Strong tasks that benefit from the main model
    if kind in ("planner", "plan", "critique", "diagnose", "review"):
        # Use main model when it differs from planner (gives better plans)
        return main_model if main_model != planner_model else planner_model

    return planner_model


# Task types that can use a lighter model
FAST_TASK_TYPES = frozenset({
    "router", "route", "intent", "format", "formatter",
    "readme", "docs", "summarizer", "summarize",
})


def pick_model_for_task(task_type: str) -> str:
    """Route to the best model for a specific task type.

    Delegates to model_config router when available (respects routing table
    in ~/.kryth/config.yaml). Falls back to legacy logic.
    """
    try:
        from agent.model_config.router import pick_model_for_task as _mc_pick
        _, model = _mc_pick(task_type)
        return model
    except Exception:
        pass

    main_model, planner_model, summarizer_model = _configured_models()
    if task_type in FAST_TASK_TYPES:
        return summarizer_model
    if task_type in ("plan", "spec", "architect"):
        return planner_model
    return main_model


def describe_routing() -> dict:
    """Diagnostic snapshot — surfaced by /diag so users can verify what
    routing is in effect."""
    main_model, planner_model, summarizer_model = _configured_models()
    result = {
        "auto_route": _auto_route_enabled(),
        "small_threshold_chars": _small_threshold(),
        "main_model": main_model,
        "planner_model": planner_model,
        "summarizer_model": summarizer_model,
    }
    # Include model_config info when available
    try:
        from agent.model_config.loader import get_config
        from agent.model_config.router import get_model_name
        cfg = get_config()
        result["config_mode"] = cfg.mode
        result["config_roles"] = {
            role: get_model_name(role)
            for role in ["main", "planner", "vision", "summary", "extraction", "reasoning"]
        }
    except Exception:
        pass
    return result


__all__ = [
    "RouteHints",
    "MAIN_MODEL",
    "PLANNER_MODEL",
    "SUMMARIZER_MODEL",
    "pick_main_model",
    "pick_helper_model",
    "describe_routing",
]
