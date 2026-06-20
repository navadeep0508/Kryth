"""DAG visualization & parallel-execution UX — presentation-only contract.

Covers: DAG-reasoning panel (Phases 7/8), DAG tree (Phase 1), parallel
efficiency (Phase 5), ownership map (Phase 3), and the Live Engine consuming
DAG_UPDATE into a rendered Execution-DAG panel. No scheduler/execution change.
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


def _capture(fn, width=180):
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


# ── Phase 7/8 — DAG reasoning panel ──────────────────────────────────────────

def test_reasoning_panel_dag():
    from agent.ui.components import dag_reasoning_panel
    from agent.mission_estimator import estimate
    out = _capture(lambda: dag_reasoning_panel(estimate("build a full-stack web app")))
    assert "Why DAG" in out
    assert "Expected speedup" in out
    assert "DAG" in out
    assert "Sequential estimate" in out and "Parallel estimate" in out


def test_reasoning_panel_direct():
    from agent.ui.components import dag_reasoning_panel
    from agent.mission_estimator import estimate
    out = _capture(lambda: dag_reasoning_panel(estimate("create a GET /users endpoint")))
    assert "Why Direct" in out
    assert "DIRECT" in out


def test_reasoning_panel_accepts_dict():
    from agent.ui.components import dag_reasoning_panel
    out = _capture(lambda: dag_reasoning_panel({
        "recommendation": "swarm", "independent_units": 6, "files": 18,
        "agents": 8, "seq_time_s": 144, "dag_time_s": 50, "speedup": 2.88,
        "reason": "high speedup",
    }))
    assert "SWARM" in out and "2.88x" in out


# ── Phase 1 — DAG tree panel ─────────────────────────────────────────────────

def _nodes():
    return [
        {"id": "db", "label": "Database", "status": "complete", "owner": "DB#1", "duration": "12s"},
        {"id": "api", "label": "Backend API", "status": "running", "deps": ["db"], "owner": "BE#1"},
        {"id": "ui", "label": "Frontend", "status": "waiting", "deps": ["api"]},
        {"id": "test", "label": "Tests", "status": "blocked", "deps": ["api", "ui"]},
    ]


def test_dag_tree_renders_nodes_and_states():
    from agent.ui.components import dag_tree_panel
    out = _capture(lambda: dag_tree_panel(_nodes()))
    assert "Execution DAG" in out
    for label in ("Database", "Backend API", "Frontend", "Tests"):
        assert label in out
    # State glyphs present
    assert "●" in out   # complete
    assert "◐" in out   # running
    assert "○" in out   # waiting
    assert "◌" in out   # blocked


def test_dag_tree_empty_is_noop():
    from agent.ui.components import dag_tree_panel
    assert _capture(lambda: dag_tree_panel([])).strip() == ""


def test_dag_tree_shows_owner_and_duration():
    from agent.ui.components import dag_tree_panel
    out = _capture(lambda: dag_tree_panel(_nodes()))
    assert "DB#1" in out and "12s" in out


# ── Phase 5 — parallel efficiency ────────────────────────────────────────────

def test_parallel_panel_efficiency():
    from agent.ui.components import parallel_panel
    out = _capture(lambda: parallel_panel(workers=12, running=9, idle=3, peak=12, queue_depth=4, work_steals=2))
    assert "Workers" in out and "12" in out
    assert "Efficiency" in out and "75%" in out


def test_parallel_panel_zero_workers_no_crash():
    from agent.ui.components import parallel_panel
    out = _capture(lambda: parallel_panel(workers=0, running=0, idle=0))
    assert "Efficiency" in out and "0%" in out


# ── Phase 3 — ownership map ──────────────────────────────────────────────────

def test_ownership_panel():
    from agent.ui.components import ownership_panel
    out = _capture(lambda: ownership_panel({"auth.py": "Backend#1", "navbar.jsx": "Frontend#1"}))
    assert "Ownership" in out
    assert "auth.py" in out and "Backend#1" in out


def test_ownership_empty_noop():
    from agent.ui.components import ownership_panel
    assert _capture(lambda: ownership_panel({})).strip() == ""


# ── Live Engine consumes DAG_UPDATE ──────────────────────────────────────────

def test_live_engine_renders_dag_from_event():
    from agent.ui.live_engine import LiveEngine
    from agent.ui.events import Event, EventKind
    eng = LiveEngine()
    eng._state.provider = "NVIDIA"
    eng._handle(Event(kind=EventKind.DAG_UPDATE, data={"nodes": _nodes()}))
    assert eng._state.dag_nodes  # state captured
    out = _capture(lambda: __import__("agent.ui.console", fromlist=["console"]).console.print(eng._render()))
    assert "Execution DAG" in out
    assert "Database" in out and "Backend API" in out
    eng.uninstall()


def test_dag_state_glyph_legend_complete():
    from agent.ui.components import _DAG_STATE
    for st in ("waiting", "running", "complete", "failed", "blocked"):
        assert st in _DAG_STATE
