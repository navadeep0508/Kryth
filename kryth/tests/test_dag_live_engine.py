"""Live DAG engine integration — Phases 6, 9, 11, 12 (presentation-only).

Critical-path/bottleneck analysis, agent-timeline + parallel-efficiency in the
Live Engine, mission replay recorder/player, and the Textual dashboard
builders. No scheduler/execution coupling anywhere.
"""

import io
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _capture(fn, width=200):
    from agent.ui.console import console
    buf = io.StringIO()
    of, ow = console.file, console._width
    try:
        console.file = buf
        console._width = width
        fn()
    finally:
        console.file = of
        console._width = ow
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


def _dag():
    return [
        {"id": "db", "label": "DB Migration", "status": "running", "duration": "30s"},
        {"id": "api", "label": "API Routes", "status": "blocked", "deps": ["db"], "duration": "20s"},
        {"id": "test", "label": "Integration Tests", "status": "blocked", "deps": ["api"], "duration": "15s"},
        {"id": "ui", "label": "Frontend", "status": "blocked", "deps": ["db"], "duration": "25s"},
    ]


# ── Phase 9 — critical path / bottleneck ─────────────────────────────────────

def test_critical_path_longest_chain():
    from agent.ui.dag_analysis import critical_path
    cp = critical_path(_dag())
    # db -> api -> test  is the longest weighted chain (30+20+15 = 65)
    assert cp.path[0] == "db"
    assert cp.labels[:1] == ["DB Migration"]
    assert "Integration Tests" in cp.labels
    assert cp.length_s == pytest.approx(65.0, abs=0.1)


def test_critical_path_bottleneck_and_impact():
    from agent.ui.dag_analysis import critical_path
    cp = critical_path(_dag())
    assert cp.bottleneck_id == "db"          # db blocks api, test, ui
    assert cp.blocked_workers == 3
    assert cp.impact == "high"


def test_critical_path_empty():
    from agent.ui.dag_analysis import critical_path
    cp = critical_path([])
    assert cp.path == [] and cp.impact == "low"


def test_critical_path_cycle_guard():
    from agent.ui.dag_analysis import critical_path
    # Pathological self/mutual dependency must not hang or crash.
    cyclic = [
        {"id": "a", "label": "A", "deps": ["b"], "status": "running"},
        {"id": "b", "label": "B", "deps": ["a"], "status": "blocked"},
    ]
    cp = critical_path(cyclic)
    assert isinstance(cp.path, list)


def test_duration_parsing_minutes_seconds():
    from agent.ui.dag_analysis import critical_path
    nodes = [
        {"id": "x", "label": "X", "duration": "1m30s", "status": "running"},
        {"id": "y", "label": "Y", "deps": ["x"], "duration": 10, "status": "blocked"},
    ]
    cp = critical_path(nodes)
    assert cp.length_s == pytest.approx(100.0, abs=0.1)  # 90 + 10


def test_critical_path_panel_renders():
    from agent.ui.components import critical_path_panel
    out = _capture(lambda: critical_path_panel(_dag()))
    assert "Critical Path" in out and "bottleneck" in out
    assert "HIGH" in out


# ── Phase 6 — agent timeline / live engine ───────────────────────────────────

def test_agent_timeline_panel_renders():
    from agent.ui.components import agent_timeline_panel
    out = _capture(lambda: agent_timeline_panel([
        {"name": "Backend#1", "step": "edit auth.py", "progress": 80, "status": "running"},
        {"name": "DB#1", "step": "migrate", "progress": 100, "status": "complete"},
    ]))
    assert "Agent Timeline" in out
    assert "Backend#1" in out and "80%" in out
    assert "█" in out


def test_agent_timeline_empty_noop():
    from agent.ui.components import agent_timeline_panel
    assert _capture(lambda: agent_timeline_panel([])).strip() == ""


def test_live_engine_tracks_agents_and_peak():
    from agent.ui.live_engine import LiveEngine
    from agent.ui.events import Event, EventKind
    eng = LiveEngine()
    eng._handle(Event(kind=EventKind.AGENT_UPDATE,
                      data={"name": "Backend#1", "status": "running", "progress": 40, "step": "auth.py"}))
    eng._handle(Event(kind=EventKind.AGENT_UPDATE,
                      data={"name": "Frontend#1", "status": "running", "progress": 20}))
    assert set(eng._state.agents) == {"Backend#1", "Frontend#1"}
    assert eng._state.peak_parallel == 2          # both running simultaneously
    eng._handle(Event(kind=EventKind.AGENT_TASK_DONE, data={"name": "Backend#1"}))
    assert eng._state.agents["Backend#1"]["progress"] == 100
    assert eng._state.agents["Backend#1"]["status"] == "complete"
    eng.uninstall()


def test_live_engine_work_steals():
    from agent.ui.live_engine import LiveEngine
    from agent.ui.events import Event, EventKind
    eng = LiveEngine()
    eng._handle(Event(kind=EventKind.WORK_STOLEN, data={"agent_id": "x"}))
    eng._handle(Event(kind=EventKind.WORK_STOLEN, data={"agent_id": "y"}))
    assert eng._state.work_steals == 2
    eng.uninstall()


def test_live_engine_renders_timeline_and_efficiency_wide():
    from agent.ui.live_engine import LiveEngine
    from agent.ui.events import Event, EventKind
    from agent.ui.console import console
    eng = LiveEngine()
    eng._state.provider = "NVIDIA"
    eng._handle(Event(kind=EventKind.DAG_UPDATE, data={"nodes": _dag()}))
    eng._handle(Event(kind=EventKind.AGENT_UPDATE,
                      data={"name": "Backend#1", "status": "running", "progress": 60}))
    out = _capture(lambda: console.print(eng._render()), width=200)
    assert "Agent Timeline" in out
    assert "Parallel Efficiency" in out
    eng.uninstall()


# ── Phase 11 — mission replay ────────────────────────────────────────────────

def test_recorder_captures_and_replays(tmp_path):
    from agent.ui.events import EventBus, EventKind
    from agent.ui.replay import MissionRecorder, load_recording, replay_mission
    bus = EventBus()
    rec = MissionRecorder("m1", directory=tmp_path, bus=bus).start()
    bus.emit(EventKind.AGENT_UPDATE, name="Backend#1", status="running", progress=10)
    bus.emit(EventKind.DAG_UPDATE, nodes=_dag())
    bus.emit(EventKind.COMPLETE, status="done", turns_used=3)
    path = rec.stop()
    assert path.exists()
    rows = load_recording(path)
    assert [r["kind"] for r in rows] == ["agent.update", "dag.update", "runtime.complete"]

    seen = []
    n = replay_mission("m1", directory=tmp_path,
                       sink=lambda k, d: seen.append((k, d)), sleep=lambda _s: None)
    assert n == 3
    assert seen[0][0] == "agent.update" and seen[0][1]["name"] == "Backend#1"
    assert seen[2][0] == "runtime.complete"


def test_replay_missing_mission_returns_zero(tmp_path):
    from agent.ui.replay import replay_mission
    assert replay_mission("nope", directory=tmp_path, sleep=lambda _s: None) == 0


def test_recorder_disabled_by_default():
    from agent.ui.replay import record_enabled
    # No env set in the default test environment.
    os.environ.pop("KRYTH_MISSION_RECORD", None)
    assert record_enabled() is False


def test_list_missions(tmp_path):
    from agent.ui.events import EventBus, EventKind
    from agent.ui.replay import MissionRecorder, list_missions
    bus = EventBus()
    MissionRecorder("alpha", directory=tmp_path, bus=bus).start().stop()
    MissionRecorder("beta", directory=tmp_path, bus=bus).start().stop()
    ids = list_missions(tmp_path)
    assert set(ids) == {"alpha", "beta"}


def test_recorder_never_raises_into_bus(tmp_path):
    # A recorder bound to the bus must not break emit even if writing fails.
    from agent.ui.events import EventBus, EventKind
    from agent.ui.replay import MissionRecorder
    bus = EventBus()
    rec = MissionRecorder("x", directory=tmp_path, bus=bus).start()
    rec._fh = None  # simulate a closed/failed file handle
    bus.emit(EventKind.LOG, message="still works")  # must not raise
    rec.stop()


# ── Phase 12 — Textual dashboard builders ────────────────────────────────────

def test_textual_builders_pure():
    from agent.ui import textual_app as tx
    dag = tx.build_dag(_dag())
    grid = tx.build_agent_grid({"Backend#1": {"name": "Backend#1", "status": "running", "progress": 50}})
    act = tx.build_activity([("write_file", "auth.py", "done")])
    assert dag is not None and grid is not None and act is not None


def test_textual_metrics_from_state():
    from agent.ui import textual_app as tx
    from agent.ui.live_engine import EngineState
    st = EngineState()
    st.agents = {"a": {"status": "running"}, "b": {"status": "running"}, "c": {"status": "idle"}}
    st.dag_nodes = _dag()
    st.peak_parallel = 2
    panel = tx.build_metrics(st)
    assert panel is not None  # builds without a running app


def test_textual_selected_flag(monkeypatch):
    from agent.ui import textual_app as tx
    monkeypatch.delenv("KRYTH_TUI", raising=False)
    monkeypatch.setenv("KRYTH_LIVE_UI", "textual")
    assert tx.tui_selected() is True
    monkeypatch.setenv("KRYTH_LIVE_UI", "1")
    monkeypatch.delenv("KRYTH_TUI", raising=False)
    assert tx.tui_selected() is False


def test_textual_launch_noop_when_not_selected(monkeypatch):
    from agent.ui import textual_app as tx
    from agent.ui.live_engine import EngineState
    monkeypatch.delenv("KRYTH_TUI", raising=False)
    monkeypatch.delenv("KRYTH_LIVE_UI", raising=False)
    # Not selected → must return False without starting an app.
    assert tx.launch(EngineState()) is False


# ── REPL: /replay command wired ──────────────────────────────────────────────

def test_replay_command_registered():
    import kryth._repl_main as r
    assert "/replay" in r.REPL_COMMANDS
    assert r._HANDLERS.get("/replay") is not None
    # Every registered command resolves to a real handler.
    assert all(r._HANDLERS.get(c) is not None for c in r.REPL_COMMANDS)
