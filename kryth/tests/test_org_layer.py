"""KRYTH organizational layer + Operations Center V2 — contract.

Portfolio, roadmap, releases, departments, hierarchy, communication, budget,
analytics, strategy, knowledge, reporting, readiness audit, and the ops-center
activation gate. All additive/pure; no execution/scheduler/orchestration change.
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


def _cap(fn, width=200):
    from agent.ui.console import console
    buf = io.StringIO()
    of, ow = console.file, console._width
    try:
        console.file, console._width = buf, width
        fn()
    finally:
        console.file, console._width = of, ow
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


# ── Phase B — portfolio ──────────────────────────────────────────────────────

def test_portfolio_runnable_respects_dependencies():
    from agent.org.portfolio import PortfolioManager, MissionState
    pm = PortfolioManager()
    pm.add_mission("api", priority=1)
    pm.add_mission("ui", priority=2, depends_on=["api"])
    assert [m.id for m in pm.runnable()] == ["api"]      # ui blocked on api
    pm.set_state("api", MissionState.COMPLETED)
    assert [m.id for m in pm.runnable()] == ["ui"]
    assert pm.blocked_by_dependency() == []


def test_portfolio_health_and_risk():
    from agent.org.portfolio import PortfolioManager, MissionState
    pm = PortfolioManager()
    pm.add_mission("a"); pm.add_mission("b")
    pm.set_state("a", MissionState.RUNNING)
    pm.update("a", risk="high", progress=40)
    h = pm.health()
    assert h["running"] == 1 and h["risk_level"] == "high"
    assert h["high_risk_missions"] == 1


def test_portfolio_progress_autocompletes():
    from agent.org.portfolio import PortfolioManager, MissionState
    pm = PortfolioManager()
    pm.add_mission("a"); pm.set_state("a", MissionState.RUNNING)
    pm.update("a", progress=100)
    assert pm.get("a").state is MissionState.COMPLETED


# ── Phase C — roadmap ────────────────────────────────────────────────────────

def test_roadmap_hierarchy_and_rollup():
    from agent.org.roadmap import Roadmap
    r = Roadmap()
    r.add("port", "portfolio")
    r.add("prog", "program", parent="port")
    r.add("epic1", "epic", parent="prog")
    r.add("m1", "mission", parent="epic1")
    r.add("m2", "mission", parent="epic1")
    r.set_progress("m1", 100); r.set_progress("m2", 0)
    assert r.progress("epic1") == 50.0           # mean of children
    assert r.progress("port") == 50.0            # rolls all the way up


def test_roadmap_rejects_bad_nesting():
    from agent.org.roadmap import Roadmap
    r = Roadmap()
    r.add("port", "portfolio")
    with pytest.raises(ValueError):
        r.add("t", "task", parent="port")        # task can't nest under portfolio


def test_roadmap_quarter_capacity():
    from agent.org.roadmap import Roadmap
    r = Roadmap()
    r.add("p", "portfolio")
    r.add("pr", "program", parent="p", quarter="2026-Q3", capacity=10)
    plan = r.capacity_plan("2026-Q3")
    assert plan["total_capacity"] == 10


# ── Phase D — releases ───────────────────────────────────────────────────────

def test_release_advances_through_stages():
    from agent.org.release import ReleasePipeline, Stage
    rp = ReleasePipeline()
    rp.create("v1", now=0)
    assert rp.advance("v1", now=1) is Stage.VERIFICATION
    rp.advance("v1", now=2); rp.advance("v1", now=3)
    assert rp.advance("v1", now=4) is Stage.PRODUCTION
    assert rp.get("v1").stage is Stage.PRODUCTION


def test_release_rollback_and_metrics():
    from agent.org.release import ReleasePipeline
    rp = ReleasePipeline()
    rp.create("v1", now=0); rp.advance("v1", now=1)
    assert rp.rollback("v1", now=2) is True
    m = rp.metrics()
    assert m["rolled_back"] == 1 and m["rollback_rate"] == 1.0


# ── Phase E — departments ────────────────────────────────────────────────────

def test_departments_utilization_and_reputation(tmp_path):
    from agent.org.departments import DepartmentRegistry
    reg = DepartmentRegistry(tmp_path / "d.json", autoload=False)
    reg.ensure("backend", capacity=4)
    reg.assign("backend", 2)
    assert reg.get("backend").utilization == 0.5
    reg.record_mission("backend", success=True)
    reg.record_mission("backend", success=True)
    reg.record_mission("backend", success=False)
    assert reg.get("backend").reputation == pytest.approx(0.667, abs=0.01)


def test_departments_persist(tmp_path):
    from agent.org.departments import DepartmentRegistry
    p = tmp_path / "d.json"
    reg = DepartmentRegistry(p, autoload=False)
    reg.record_mission("frontend", success=True)
    reg.save()
    reg2 = DepartmentRegistry(p)
    assert reg2.get("frontend").missions_succeeded == 1


# ── Phase F — hierarchy ──────────────────────────────────────────────────────

def test_hierarchy_productivity_rollup():
    from agent.org.hierarchy import Hierarchy
    h = Hierarchy()
    h.add("head", "head", department="backend")
    h.add("lead", "lead", parent="head")
    h.add("w1", "worker", parent="lead")
    h.add("w2", "worker", parent="lead")
    h.record("w1", done=8, failed=2)     # 0.8
    h.record("w2", done=6, failed=4)     # 0.6
    assert h.team_productivity("lead") == pytest.approx(0.7, abs=0.01)
    assert h.leadership_effectiveness("head") == pytest.approx(0.7, abs=0.01)


def test_hierarchy_rejects_bad_reporting():
    from agent.org.hierarchy import Hierarchy
    h = Hierarchy()
    h.add("head", "head")
    with pytest.raises(ValueError):
        h.add("w", "worker", parent="head")   # worker can't report to head directly


# ── Phase G — communication ──────────────────────────────────────────────────

def test_communication_log_and_replay():
    from agent.org.communication import CommunicationLog, MessageKind
    log = CommunicationLog()
    log.status("w1", "worker", "scaffolding", mission_id="m1", at=1)
    log.blocker("w1", "worker", "needs auth", mission_id="m1", at=2)
    log.decision("exec", "executive", "spawn 2 workers", at=3, reason="queue>12")
    assert len(log.decisions()) == 1
    assert len(log.open_blockers()) == 1
    seen = []
    n = log.replay(lambda d: seen.append(d), mission_id="m1")
    assert n == 2 and seen[0]["kind"] == "status"


# ── Phase H — budget ─────────────────────────────────────────────────────────

def test_budget_enforcement_and_forecast():
    from agent.org.budget import BudgetManager
    bm = BudgetManager()
    bm.set_budget("token", "mission-a", 1000, enforce=True)
    assert bm.spend("token", "mission-a", 600) is True
    assert bm.can_spend("token", "mission-a", 500) is False    # would exceed
    assert bm.spend("token", "mission-a", 500) is False        # enforced (not recorded)
    assert bm.spend("token", "mission-a", 250) is True         # 850/1000 → 85%
    f = bm.forecast("token", "mission-a", elapsed=6, horizon=6)
    assert f["projected"] > 850     # linear projection forward
    assert any(w["status"] in ("warning", "critical") for w in bm.warnings())


# ── Phase I/K — analytics + strategy ─────────────────────────────────────────

def test_analytics_top_department(tmp_path):
    from agent.org.departments import DepartmentRegistry
    from agent.org.analytics import top_department
    reg = DepartmentRegistry(tmp_path / "d.json", autoload=False, seed_defaults=False)
    reg.record_mission("backend", success=True)
    reg.record_mission("frontend", success=False)
    assert top_department(reg)["name"] == "backend"


def test_strategy_recommends_but_never_executes():
    from agent.org.strategy import recommend, StrategySignal
    recs = recommend(StrategySignal(target="m1", queue_depth=20, idle_workers=0,
                                    workers=4, risk="high", independent_units=8,
                                    blocked_ratio=0.6))
    assert recs
    assert all(r.auto_execute is False for r in recs)
    actions = {r.action for r in recs}
    assert "hire_workers" in actions and "escalate_mission" in actions


# ── Phase J — knowledge ──────────────────────────────────────────────────────

def test_knowledge_capitalization_and_reuse():
    from agent.org.knowledge import KnowledgeBase, AssetKind
    kb = KnowledgeBase()
    assets = kb.capitalize({"id": "m1", "category": "saas", "components": ["frontend", "backend"],
                            "sections": ["hero", "pricing"], "has_tests": True})
    assert len(assets) >= 4
    comp = kb.find(kind=AssetKind.COMPONENT)
    assert comp
    kb.reuse(comp[0].id)
    m = kb.metrics()
    assert m["missions_capitalized"] == 1 and m["total_reuses"] == 1


# ── Phase L/O — reporting + audit ────────────────────────────────────────────

def test_reporting_executive_and_markdown():
    from agent.org.portfolio import PortfolioManager
    from agent.org.reporting import executive_report, to_markdown, to_json
    pm = PortfolioManager(); pm.add_mission("a", budget_tokens=1000)
    rep = executive_report(portfolio=pm)
    assert rep["report"] == "executive" and "portfolio_health" in rep
    assert "Executive" in to_markdown(rep)
    assert "executive" in to_json(rep)


def test_readiness_audit_overall_and_gaps():
    from agent.org.readiness_audit import MaturityInputs, audit, audit_report
    r = audit(MaturityInputs(architecture=90, operational=60, organizational=75,
                             release=80, portfolio=85, department=65, budget=60))
    assert 0 <= r.overall <= 100
    assert any("operational" in g for g in r.gaps)
    assert r.roadmap            # lowest dims surfaced
    assert "Enterprise Readiness" in audit_report(
        MaturityInputs(architecture=90, operational=90, organizational=90,
                       release=90, portfolio=90, department=90, budget=90))


# ── Operations Center — ACTIVATION GATE (the critical scope limit) ───────────

@pytest.mark.parametrize("kw", [
    {"is_conversational": True, "mode": "swarm", "workers": 20},   # conversation wins
    {"mode": "direct", "workers": 1},
    {"mode": "fast", "workers": 1},
    {"mode": "conversation"},
    {"mode": "", "workers": 1},                                    # single agent
    {"mode": "direct", "workers": 8},                              # direct even if many
])
def test_ops_center_does_not_activate_for_simple(kw):
    from agent.ui.ops_center import should_activate
    assert should_activate(**kw) is False


@pytest.mark.parametrize("kw", [
    {"mode": "dag", "workers": 6},
    {"mode": "swarm", "workers": 20},
    {"portfolio": True, "workers": 3},
    {"multi_agent": True, "workers": 4},
])
def test_ops_center_activates_for_parallel(kw):
    from agent.ui.ops_center import should_activate
    assert should_activate(**kw) is True


def test_progressive_disclosure_tiers():
    from agent.ui.ops_center import disclosure_level
    assert disclosure_level(1) == "minimal"
    assert disclosure_level(3) == "team"
    assert disclosure_level(10) == "ops_center"
    assert disclosure_level(20) == "org"


def test_ops_center_active_requires_env(monkeypatch):
    from agent.ui import ops_center as oc
    monkeypatch.delenv("KRYTH_OPS_CENTER", raising=False)
    assert oc.active(mode="swarm", workers=10) is False     # off by default
    monkeypatch.setenv("KRYTH_OPS_CENTER", "1")
    assert oc.active(mode="swarm", workers=10) is True
    assert oc.active(mode="direct", workers=1) is False     # gate still applies
    assert oc.active(mode="dag", workers=1) is False        # minimal tier → no center


# ── Operations Center — views render ─────────────────────────────────────────

def test_ops_header_renders():
    from agent.ui.ops_center import operations_header
    out = _cap(lambda: operations_header(active_missions=3, running_workers=9,
                                         utilization=0.75, bottlenecks=1, risk="medium",
                                         budget_usage=0.4, success_rate=0.92, eta_min=12))
    assert "OPERATIONS CENTER" in out and "75%" in out and "MEDIUM" in out


def test_portfolio_and_warroom_render():
    from agent.ui.ops_center import portfolio_view, war_room
    out = _cap(lambda: portfolio_view([
        {"id": "saas", "title": "SaaS", "progress": 82, "state": "running", "risk": "medium", "eta_min": 8},
    ]))
    assert "Mission Portfolio" in out and "82%" in out
    wr = _cap(lambda: war_room(mission="CRM", risk="high", delay_min=12, blocked_teams=5,
                               root_cause="Auth Service", recovery_actions=["spawn auth worker"]))
    assert "WAR ROOM" in wr and "Auth Service" in wr


def test_economics_and_digital_twin_render():
    from agent.ui.ops_center import economics_view, digital_twin, dependency_radar
    out = _cap(lambda: economics_view(tokens_used=50000, tokens_remaining=50000,
                                      llm_calls=40, tool_calls=120, workers=9,
                                      elapsed_min=10, remaining_min=8, deliverables=5))
    assert "Economics" in out and "Efficiency" in out
    dt = _cap(lambda: digital_twin([{"name": "frontend", "workload": 12, "utilization": 0.8}]))
    assert "Digital Twin" in dt and "frontend" in dt
    dr = _cap(lambda: dependency_radar({"dependency": "auth", "blocking": 4,
                                        "workers_waiting": 3, "impact": "high"},
                                       consumers=["Login UI", "Dashboard"]))
    assert "Dependency Radar" in dr and "auth" in dr and "HIGH" in dr
