"""Mission Cost Estimator + DAG Eligibility Engine contract.

DAG/swarm orchestration must NOT be the default for every complex task — only
where parallel speedup justifies the coordination overhead. The estimator is a
pure function; the agent loop uses it as a conservative gate (downgrade-only),
and a user /mode override always wins.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.mission_estimator import estimate, normalize_mode, render


# ── Multi-component builds → DAG/SWARM (still orchestrate) ──────────────────

@pytest.mark.parametrize("prompt", [
    "build a full-stack web app",
    "build a full stack task manager",
    "build a SaaS starter",
    "build a web app with frontend backend and database and tests",
    "build an e-commerce marketplace",
])
def test_multicomponent_builds_recommend_parallel(prompt):
    e = estimate(prompt)
    assert e.recommendation in ("dag", "swarm"), (prompt, e.to_dict())
    assert e.independent_units >= 2


# ── Single-component / low-parallelism → DIRECT (skip orchestration) ─────────

@pytest.mark.parametrize("prompt", [
    "create a GET /users endpoint",
    "build blog backend",
    "refactor this complex function",
    "create hello.txt",
    "fix the bug in main.py",
])
def test_low_parallelism_recommends_direct(prompt):
    e = estimate(prompt)
    assert e.recommendation == "direct", (prompt, e.to_dict())


# ── Graph-driven routing (the redesign) ──────────────────────────────────────
# Work is grouped by ownership DOMAIN; parallelism comes from the dependency
# graph between domains — NOT from counting sections. A landing page with many
# sections is ONE frontend owner → DIRECT, not a multi-agent DAG.

@pytest.mark.parametrize("prompt,rec", [
    ("create hello.txt", "direct"),
    ("create a GET /users endpoint", "direct"),
    ("create a SaaS landing page with hero, features, pricing, FAQ, testimonials, animations", "direct"),
    ("build a marketing landing page with hero, pricing, testimonials, FAQ", "direct"),
    ("build a full-stack web app with frontend backend database and tests", "dag"),
    ("build a full SaaS platform with auth payments dashboard docs and tests", "dag"),
])
def test_graph_driven_routing(prompt, rec):
    e = estimate(prompt)
    assert e.recommendation == rec, (prompt, e.to_dict())


def test_landing_page_is_one_agent_not_dag():
    # The core correction: a multi-section landing page is ONE frontend owner.
    e = estimate("create a landing page with a hero section, features, pricing and a FAQ")
    assert e.recommendation == "direct"
    assert e.agents == 1                      # NOT one agent per section
    assert e.independent_units == 1


def test_multi_domain_orchestrates_with_one_agent_per_domain():
    e = estimate("build a full-stack web app with frontend backend database and tests")
    assert e.recommendation in ("dag", "swarm")
    assert 3 <= e.agents <= 6                 # agents == domains, no inflation
    assert e.complexity_score > 0


# ── Density-driven decision function ─────────────────────────────────────────

def test_density_decision_logic():
    from agent.mission_estimator import _recommend
    # none/low → direct regardless of speedup
    assert _recommend("none", 5.0, 1, "low")[0] == "direct"
    assert _recommend("low", 5.0, 2, "low")[0] == "direct"
    # medium → speedup breaks the tie
    assert _recommend("medium", 1.0, 2, "medium")[0] == "direct"
    assert _recommend("medium", 1.5, 3, "medium")[0] == "dag"
    # high → dag even when speedup looks low (single-file but decomposable)
    assert _recommend("high", 0.9, 5, "high")[0] == "dag"
    # very_high → swarm
    assert _recommend("very_high", 3.0, 10, "very_high")[0] == "swarm"


# ── Dynamic agent scaling (Phase 4) ──────────────────────────────────────────

def test_agent_scaling_grows_with_files():
    small = estimate("create a.txt")
    big = estimate("build a full stack saas platform with frontend backend database auth payments and tests")
    assert small.agents <= big.agents
    assert small.agents >= 1
    assert big.agents <= 16  # bounded


# ── Estimate fields + render ─────────────────────────────────────────────────

def test_estimate_fields_present():
    e = estimate("build a full-stack web app")
    d = e.to_dict()
    for k in ("files", "independent_units", "agents", "tokens", "seq_time_s",
              "dag_time_s", "speedup", "recommendation",
              "parallel_density", "decomposition_potential", "complexity_score"):
        assert k in d
    assert e.tokens > 0
    assert e.parallel_density in ("none", "very_low", "low", "medium", "high", "very_high")


def test_render_is_readable():
    out = render(estimate("build a full-stack web app"))
    assert "Decision" in out and "speedup" in out.lower()
    assert "density" in out.lower() and "Decomposition" in out


# ── /mode normalization (user override) ──────────────────────────────────────

@pytest.mark.parametrize("m,exp", [
    ("direct", "direct"), ("DAG", "dag"), (" swarm ", "swarm"),
    ("auto", "auto"), ("nonsense", None), ("", None),
])
def test_mode_normalization(m, exp):
    assert normalize_mode(m) == exp


# ── Integration: gate downgrades low-parallelism complex tasks ───────────────

def test_eligibility_gate_downgrades_then_runs_direct(monkeypatch):
    # A "complex" classification with low parallel speedup must skip
    # orchestration and run the single-agent inner loop instead.
    import agent.agent_loop as al
    import agent.task_classifier as tc
    tc._llm_tiebreaker = lambda *a, **k: None
    monkeypatch.setenv("KRYTH_DAG_ELIGIBILITY", "1")

    orchestrated = {"v": False}
    inner = {"v": False}
    from unittest.mock import MagicMock

    monkeypatch.setattr(al, "classify_task",
                        lambda *a, **k: MagicMock(complexity="complex", is_conversational=False,
                                                  category="coding", reason="x"))
    if getattr(al, "orchestrate", None) is not None:
        monkeypatch.setattr(al, "orchestrate",
                            lambda *a, **k: (orchestrated.__setitem__("v", True) or MagicMock(approved=False, output="", explanation="", mode_updated=None)))
    monkeypatch.setattr(al, "run_inner_loop",
                        lambda *a, **k: (inner.__setitem__("v", True) or _DoneResult()))
    monkeypatch.setattr(al, "route", lambda *a, **k: [])
    monkeypatch.setattr(al, "auto_select_skills", lambda *a, **k: [])
    monkeypatch.setattr(al, "build_initial_system", lambda *a, **k: None)
    monkeypatch.setattr(al, "_speculative_preload", lambda *a, **k: _set_event())
    import agent.ui as ui
    for n in dir(ui):
        f = getattr(ui, n, None)
        if callable(f) and not n.startswith("_"):
            try:
                monkeypatch.setattr(ui, n, lambda *a, **k: None)
            except Exception:
                pass
    try:
        import agent.orchestration.worker_pool as wp
        monkeypatch.setattr(wp, "spawn_early_for_prompt", lambda *a, **k: {})
    except Exception:
        pass
    from agent.session import get_session
    s = get_session(); s.messages = []; s.exec_mode = "auto"

    # single backend domain → DIRECT → must NOT orchestrate.
    al.run_agent("create a GET /users endpoint")
    assert orchestrated["v"] is False
    assert inner["v"] is True


def test_mode_override_forces_orchestration(monkeypatch):
    # /mode dag must force orchestration even on a low-parallelism task.
    import agent.agent_loop as al
    import agent.task_classifier as tc
    tc._llm_tiebreaker = lambda *a, **k: None
    from unittest.mock import MagicMock
    orchestrated = {"v": False}
    monkeypatch.setattr(al, "classify_task",
                        lambda *a, **k: MagicMock(complexity="complex", is_conversational=False,
                                                  category="coding", reason="x"))
    if getattr(al, "orchestrate", None) is None:
        pytest.skip("orchestrate unavailable")
    monkeypatch.setattr(al, "orchestrate",
                        lambda *a, **k: (orchestrated.__setitem__("v", True) or MagicMock(approved=True, output="done", explanation="", mode_updated=None)))
    monkeypatch.setattr(al, "run_inner_loop", lambda *a, **k: _DoneResult())
    monkeypatch.setattr(al, "route", lambda *a, **k: [])
    monkeypatch.setattr(al, "auto_select_skills", lambda *a, **k: [])
    monkeypatch.setattr(al, "build_initial_system", lambda *a, **k: None)
    monkeypatch.setattr(al, "_speculative_preload", lambda *a, **k: _set_event())
    import agent.ui as ui
    for n in dir(ui):
        f = getattr(ui, n, None)
        if callable(f) and not n.startswith("_"):
            try:
                monkeypatch.setattr(ui, n, lambda *a, **k: None)
            except Exception:
                pass
    try:
        import agent.orchestration.worker_pool as wp
        monkeypatch.setattr(wp, "spawn_early_for_prompt", lambda *a, **k: {})
    except Exception:
        pass
    from agent.session import get_session
    s = get_session(); s.messages = []; s.exec_mode = "dag"   # user override
    al.run_agent("build JWT authentication")
    assert orchestrated["v"] is True


def _set_event():
    import threading
    e = threading.Event(); e.set(); return e


class _DoneResult:
    status = "done"; content = "ok"; turns_used = 0
