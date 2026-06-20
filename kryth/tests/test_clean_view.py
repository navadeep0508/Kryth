"""Clean execution visibility — operations-center view filter.

RULE 1: during orchestrated (DAG/SWARM) missions the default UI must NOT show
tool calls, reasoning, planning, or log spam unless /agents /logs /debug is open.
RULE 2: organizational events (mission/team/progress/risk/ownership) always show.
Outside orchestration (conversation/direct/single agent) nothing is filtered.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.ui import clean_view as cv
from agent.ui.events import EventKind


@pytest.fixture(autouse=True)
def _reset():
    cv.set_orchestrated(False)
    cv._STATE.set_detail(())
    yield
    cv.set_orchestrated(False)
    cv._STATE.set_detail(())


# ── RULE 1 — internals hidden during orchestration ───────────────────────────

INTERNAL = [
    EventKind.TOOL_START, EventKind.TOOL_RESULT, EventKind.LLM_REASONING_CHUNK,
    EventKind.LLM_WAITING, EventKind.PLAN, EventKind.PLAN_PROSE, EventKind.LOG,
    EventKind.DIFF, EventKind.SHELL_RUN,
]


@pytest.mark.parametrize("kind", INTERNAL)
def test_internals_hidden_during_orchestration(kind):
    cv.set_orchestrated(True)
    assert cv.should_render(kind) is False


@pytest.mark.parametrize("kind", INTERNAL)
def test_internals_shown_when_detail_open(kind):
    cv.set_orchestrated(True)
    cv.open_detail_view("logs")
    assert cv.should_render(kind) is True
    cv.close_detail_view("logs")
    assert cv.should_render(kind) is False


# ── RULE 2 — organizational events always show ───────────────────────────────

ORG = [
    EventKind.MISSION_PROGRESS, EventKind.AGENT_UPDATE, EventKind.DAG_UPDATE,
    EventKind.QUEUE_STATUS, EventKind.TIMELINE_EVENT, EventKind.RUN_SUMMARY,
    EventKind.MISSION_COMPLETE, EventKind.AGENT_TASK_DONE,
]


@pytest.mark.parametrize("kind", ORG)
def test_org_events_always_shown(kind):
    cv.set_orchestrated(True)
    assert cv.should_render(kind) is True


def test_errors_always_shown():
    cv.set_orchestrated(True)
    for kind in (EventKind.TOOL_ERROR, EventKind.LLM_ERROR, EventKind.TOOL_DENIED):
        assert cv.should_render(kind) is True


# ── Outside orchestration nothing is filtered ────────────────────────────────

@pytest.mark.parametrize("kind", INTERNAL)
def test_no_filtering_outside_orchestration(kind):
    cv.set_orchestrated(False)
    assert cv.should_render(kind) is True


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("KRYTH_CLEAN_VIEW", "0")
    cv.set_orchestrated(True)
    assert cv.should_render(EventKind.TOOL_START) is True   # escape hatch


def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv("KRYTH_CLEAN_VIEW", raising=False)
    assert cv.clean_view_enabled() is True


# ── Renderer integration: filter is applied at dispatch ──────────────────────

def test_dispatch_suppresses_internal(monkeypatch):
    import agent.ui.renderer as r
    from agent.ui.events import Event
    called = {"n": 0}
    monkeypatch.setitem(r._HANDLERS, EventKind.TOOL_START, lambda e: called.__setitem__("n", called["n"] + 1))
    cv.set_orchestrated(True)
    r._dispatch(Event(kind=EventKind.TOOL_START, data={"name": "list_files"}))
    assert called["n"] == 0                  # suppressed in clean view
    cv.open_detail_view("agents")
    r._dispatch(Event(kind=EventKind.TOOL_START, data={"name": "list_files"}))
    assert called["n"] == 1                  # shown when /agents open


def test_dispatch_passes_org_event(monkeypatch):
    import agent.ui.renderer as r
    from agent.ui.events import Event
    seen = {"n": 0}
    monkeypatch.setitem(r._HANDLERS, EventKind.DAG_UPDATE, lambda e: seen.__setitem__("n", seen["n"] + 1))
    cv.set_orchestrated(True)
    r._dispatch(Event(kind=EventKind.DAG_UPDATE, data={"nodes": []}))
    assert seen["n"] == 1                     # org event always rendered


# ── REPL detail-view commands wired ──────────────────────────────────────────

def test_repl_detail_commands_registered():
    import kryth._repl_main as rp
    for c in ("/agents", "/logs", "/debug"):
        assert c in rp.REPL_COMMANDS
        assert rp._HANDLERS.get(c) is not None
