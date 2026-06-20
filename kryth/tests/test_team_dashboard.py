"""Orchestration team dashboard — isolated panels, no shared log stream.

DAG/SWARM execution renders one panel per team (+ executive, dependency, tool
activity), aggregated from scheduler events — never an interleaved worker log.
"""

import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.ui import team_dashboard as td
from agent.ui.events import EventKind


def _cap(renderable, width=200):
    from agent.ui.console import console
    buf = io.StringIO()
    of, ow = console.file, console._width
    try:
        console.file, console._width = buf, width
        console.print(renderable)
    finally:
        console.file, console._width = of, ow
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


def _seed(state):
    for role in ("Frontend Team", "Backend Team", "Database Team"):
        state.apply(EventKind.AGENT_CREATED, {"name": role})
    state.apply(EventKind.AGENT_TASK_START, {"name": "Frontend Team", "description": "Dashboard"})
    state.apply(EventKind.AGENT_UPDATE, {"name": "Frontend Team", "progress": 45, "status": "running"})
    state.apply(EventKind.AGENT_UPDATE, {"name": "Backend Team", "progress": 30, "status": "running", "task": "API Layer"})
    state.apply(EventKind.AGENT_UPDATE, {"name": "Database Team", "progress": 75, "status": "running", "task": "Migrations"})


# ── State aggregation ────────────────────────────────────────────────────────

def test_agents_aggregate_into_teams():
    s = td.TeamDashboardState()
    _seed(s)
    assert set(s.teams) == {"Frontend Team", "Backend Team", "Database Team"}
    assert s.teams["Frontend Team"].progress == 45
    assert s.teams["Frontend Team"].current == "Dashboard"
    assert s.teams["Backend Team"].status == "ACTIVE"


def test_task_done_completes_team():
    s = td.TeamDashboardState()
    s.apply(EventKind.AGENT_CREATED, {"name": "Database Team"})
    s.apply(EventKind.AGENT_TASK_DONE, {"name": "Database Team"})
    assert s.teams["Database Team"].status == "COMPLETE"
    assert s.teams["Database Team"].progress == 100


def test_overall_progress_and_utilization():
    s = td.TeamDashboardState()
    _seed(s)
    assert s.overall_progress() == round((45 + 30 + 75) / 3)
    assert s.utilization() == 100         # all three active


def test_tool_actions_attributed_to_last_team():
    s = td.TeamDashboardState()
    s.apply(EventKind.AGENT_TASK_START, {"name": "Frontend Team", "description": "Hero"})
    s.apply(EventKind.TOOL_START, {"name": "write_file", "args": {"path": "hero.tsx"}})
    assert list(s.recent)[-1] == ("Frontend Team", "write_file hero.tsx")


def test_recent_actions_capped():
    s = td.TeamDashboardState()
    s.apply(EventKind.AGENT_TASK_START, {"name": "Backend Team"})
    for i in range(20):
        s.apply(EventKind.TOOL_START, {"name": "edit_file", "args": {"path": f"f{i}.py"}})
    assert len(s.recent) <= 10            # no scrolling spam


def test_dag_update_feeds_dependency_panel():
    s = td.TeamDashboardState()
    s.apply(EventKind.DAG_UPDATE, {"nodes": [
        {"id": "db", "label": "Database Team", "status": "complete"},
        {"id": "be", "label": "Backend Team", "status": "running", "deps": ["db"]},
    ]})
    assert len(s.deps) == 2


# ── Isolated-panel rendering ─────────────────────────────────────────────────

def test_render_has_isolated_team_panels():
    s = td.TeamDashboardState(eta_min=4, risk="low")
    _seed(s)
    out = _cap(td.render_dashboard(s))
    # one panel per team
    assert "FRONTEND TEAM" in out and "BACKEND TEAM" in out and "DATABASE TEAM" in out
    # executive panel present
    assert "EXECUTIVE" in out and "Risk" in out and "ETA" in out
    # progress shown as a bar, not a log line
    assert "45%" in out


def test_render_tool_activity_panel():
    s = td.TeamDashboardState()
    s.apply(EventKind.AGENT_TASK_START, {"name": "Database Team"})
    s.apply(EventKind.TOOL_START, {"name": "write_file", "args": {"path": "schema.prisma"}})
    out = _cap(td.render_dashboard(s))
    assert "RECENT ACTIONS" in out
    assert "Database Team: write_file schema.prisma" in out


def test_render_no_raw_log_stream():
    # The dashboard must NOT contain interleaved "Agent A / Agent B" stream lines.
    s = td.TeamDashboardState()
    _seed(s)
    out = _cap(td.render_dashboard(s))
    # team names appear as panel titles exactly once each (not repeated stream entries)
    assert out.count("FRONTEND TEAM") == 1
    assert out.count("BACKEND TEAM") == 1


# ── Clean-view: dashboard owns the screen ────────────────────────────────────

def test_dashboard_active_silences_incremental_renderer():
    from agent.ui import clean_view as cv
    cv.set_orchestrated(True)
    cv.set_dashboard_active(True)
    try:
        # With the dashboard live, the incremental renderer paints nothing —
        # even org events go through the dashboard subscriber instead.
        assert cv.should_render(EventKind.AGENT_UPDATE) is False
        assert cv.should_render(EventKind.TOOL_START) is False
        assert cv.should_render(EventKind.DAG_UPDATE) is False
    finally:
        cv.set_dashboard_active(False)
        cv.set_orchestrated(False)


def test_detail_view_overrides_dashboard():
    from agent.ui import clean_view as cv
    cv.set_orchestrated(True)
    cv.set_dashboard_active(True)
    cv.open_detail_view("logs")
    try:
        assert cv.should_render(EventKind.TOOL_START) is True   # /logs open
    finally:
        cv.close_detail_view("logs")
        cv.set_dashboard_active(False)
        cv.set_orchestrated(False)


# ── Live controller is non-blocking / headless-safe ──────────────────────────

def test_dashboard_controller_headless_safe():
    # In the (non-TTY) test environment, start() must aggregate but not open a
    # Live terminal — and must never raise.
    from agent.ui.events import BUS
    dash = td.OrchestrationDashboard(eta_min=3).start()
    BUS.emit(EventKind.AGENT_CREATED, name="Frontend Team")
    BUS.emit(EventKind.AGENT_UPDATE, name="Frontend Team", progress=50, status="running")
    dash.stop()
    assert dash.state.teams["Frontend Team"].progress == 50
    assert dash._live is None              # no live terminal in tests


# ── Scheduler dashboard: per-agent boxes, humanized actions, no raw logs ─────

def test_humanize_action_no_raw_logs():
    from agent.ui.dashboard import _humanize_action
    assert _humanize_action("write_file", "schema.prisma") == "Creating schema.prisma"
    assert _humanize_action("edit_file", "auth.ts") == "Editing auth.ts"
    assert _humanize_action("list_files", "") == "Scanning project"
    assert _humanize_action("todo_write", "x") == "Planning"
    # run_command must NOT echo the full command line — just a short token
    out = _humanize_action("run_command", "npx prisma migrate dev --name init")
    assert out.startswith("Running") and "--name" not in out


def test_agent_node_tracks_current_and_history():
    import agent.ui.dashboard as d
    d.start_dashboard(goal="x", total_agents=1, total_layers=1)
    d._handle({"kind": "agent_update", "id": "Database Team", "role": "Database Team", "status": "running"})
    d._handle({"kind": "tool_used", "agent": "Database Team", "tool": "write_file", "detail": "schema.prisma"})
    d._handle({"kind": "tool_used", "agent": "Database Team", "tool": "run_command", "detail": "prisma migrate"})
    node = d._state.agents["Database Team"]
    assert node.current.startswith("Running")             # latest action
    assert any("schema.prisma" in h for h in node.history)  # prior rolled into history
    d.stop_dashboard()

