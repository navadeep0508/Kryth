"""Clean execution view — operations-center visibility filter.

During orchestrated (DAG/SWARM) execution the main UI must show the
*organization* (mission, teams, progress, ETA, risk, blockers, ownership) — NOT
agent internals (tool calls, reasoning, planning, analysis, log spam). This
module decides, per UI event, whether it belongs in the clean default view.

It is presentation-only: it filters what the renderer paints; it never changes
execution, routing, or what events are emitted. Internals are not lost — they
are still logged and become visible when the user opens a detail view.

Activation:
  • Only filters while an orchestrated mission is active (`set_orchestrated`).
  • `KRYTH_CLEAN_VIEW=0` disables it entirely (escape hatch).
  • Detail views (`/agents`, `/logs`, `/debug`) re-expose internals via
    `set_detail_view`.
Conversation, direct mode, and single-agent tasks are NEVER filtered — they keep
the current UI exactly.
"""

from __future__ import annotations

import os
import threading

from agent.ui.events import EventKind


# Event kinds that are INTERNAL — hidden in the clean view (tool spam, reasoning,
# planning, analysis, generic logs). Everything not listed is organizational and
# always shown (mission/team/progress/risk/ownership/approval/errors).
_INTERNAL_KINDS = frozenset({
    EventKind.TOOL_START, EventKind.TOOL_RESULT, EventKind.TOOL_COERCED,
    EventKind.LLM_REASONING_START, EventKind.LLM_REASONING_CHUNK, EventKind.LLM_REASONING_END,
    EventKind.LLM_CONTENT_START, EventKind.LLM_CONTENT_CHUNK, EventKind.LLM_CONTENT_END,
    EventKind.LLM_WAITING, EventKind.LLM_USAGE, EventKind.LLM_HERMES_RECOVERY,
    EventKind.LLM_DEGENERATE, EventKind.LLM_RETRY,
    EventKind.PLAN, EventKind.PLAN_PROSE,
    EventKind.WRITE_PREVIEW, EventKind.DIFF, EventKind.SHELL_RUN, EventKind.SHELL_END,
    EventKind.TODOS, EventKind.LOG,
})

# Kinds that ALWAYS pass, even mid-orchestration — operational signal the user
# must see (errors, denials, mission/team/agent status, the org dashboards).
_ALWAYS_SHOW = frozenset({
    EventKind.TOOL_ERROR, EventKind.TOOL_DENIED, EventKind.TOOL_HOOK_BLOCKED,
    EventKind.LLM_ERROR,
    EventKind.MISSION_START, EventKind.MISSION_PROGRESS, EventKind.MISSION_COMPLETE,
    EventKind.MISSION_FAILED, EventKind.AGENT_UPDATE, EventKind.AGENT_CREATED,
    EventKind.AGENT_TASK_START, EventKind.AGENT_TASK_DONE, EventKind.AGENT_FAILED,
    EventKind.QUEUE_STATUS, EventKind.WORK_STOLEN, EventKind.DAG_UPDATE,
    EventKind.TIMELINE_EVENT, EventKind.APPROVAL_BATCH, EventKind.RUN_SUMMARY,
    EventKind.BANNER, EventKind.TURN_INTERRUPTED, EventKind.TURN_MAX_TURNS,
})


class _ViewState:
    """Thread-safe flags for the clean-view filter."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._orchestrated = False     # an orchestrated mission is running
        self._dashboard = False        # the live team dashboard owns the screen
        self._detail = set()           # open detail views: agents | logs | debug

    def set_dashboard(self, on: bool) -> None:
        with self._lock:
            self._dashboard = bool(on)

    def dashboard(self) -> bool:
        with self._lock:
            return self._dashboard

    def set_orchestrated(self, on: bool) -> None:
        with self._lock:
            self._orchestrated = bool(on)

    def orchestrated(self) -> bool:
        with self._lock:
            return self._orchestrated

    def open_detail(self, view: str) -> None:
        with self._lock:
            self._detail.add(view)

    def close_detail(self, view: str) -> None:
        with self._lock:
            self._detail.discard(view)

    def set_detail(self, views) -> None:
        with self._lock:
            self._detail = set(views or ())

    def detail_open(self) -> bool:
        with self._lock:
            return bool(self._detail)

    def detail_views(self):
        with self._lock:
            return set(self._detail)


_STATE = _ViewState()


def clean_view_enabled() -> bool:
    """Master switch — default ON; KRYTH_CLEAN_VIEW=0 disables."""
    return os.environ.get("KRYTH_CLEAN_VIEW", "1").strip().lower() not in ("0", "false", "no", "off")


# Public state mutators (called by the orchestrator + REPL commands).
def set_orchestrated(on: bool) -> None:
    _STATE.set_orchestrated(on)


def set_dashboard_active(on: bool) -> None:
    """The live team dashboard owns the terminal — incremental renderer goes
    fully silent (the dashboard's own subscriber paints the panels)."""
    _STATE.set_dashboard(on)


def dashboard_active() -> bool:
    return _STATE.dashboard()


def open_detail_view(view: str) -> None:
    """`/agents`, `/logs`, `/debug` → re-expose internals."""
    _STATE.open_detail(view)


def close_detail_view(view: str) -> None:
    _STATE.close_detail(view)


def detail_open() -> bool:
    return _STATE.detail_open()


def should_render(kind: EventKind) -> bool:
    """True if this event belongs in the current view.

    Clean view hides INTERNAL kinds ONLY while an orchestrated mission is active
    and no detail view is open. Outside orchestration (conversation/direct/single
    agent) nothing is filtered — the UI is unchanged.
    """
    if not clean_view_enabled():
        return True
    # The live team dashboard owns the screen — the incremental renderer must
    # paint NOTHING (the dashboard subscriber renders the panels). Detail views
    # still let internals through (the user explicitly opened a pane).
    if _STATE.dashboard() and not _STATE.detail_open():
        return False
    if not _STATE.orchestrated():
        return True                      # not a DAG/SWARM mission — show everything
    if _STATE.detail_open():
        return True                      # /agents /logs /debug open — show internals
    if kind in _ALWAYS_SHOW:
        return True
    return kind not in _INTERNAL_KINDS
