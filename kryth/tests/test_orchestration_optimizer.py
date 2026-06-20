"""Graph-driven orchestration optimizer — contract.

The redesign's core correction: work is grouped by ownership DOMAIN, parallelism
comes from the dependency graph between domains, and agents are NEVER inflated
per section/page. Pure/deterministic.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.orchestration_optimizer import (
    optimize, parse_work, DependencyGraph, analyze_complexity,
    allocate_agents, select_mode, validate_plan, render_report,
)


# ── Anti-inflation: sections are ONE frontend agent, not many ────────────────

def test_landing_page_sections_collapse_to_one_frontend_agent():
    p = optimize("create a SaaS landing page with hero, features, pricing, FAQ, "
                 "testimonials, contact, team, blog, footer")
    # The whole point of the redesign: NOT 9 agents.
    assert p.allocation.count == 1
    assert p.allocation.agents[0]["domain"] == "frontend"
    assert len(p.allocation.agents[0]["work_items"]) >= 5   # sections are work items
    assert p.mode == "direct"                                # one owner → no orchestration
    assert p.graph.parallelism_class in ("none", "very_low")


def test_no_agent_per_section_inflation():
    p = optimize("build a marketing page with hero, pricing, faq, testimonials, contact")
    assert p.allocation.count == 1            # not 5
    assert p.validation.valid


# ── Mode selection (reasoned) ────────────────────────────────────────────────

@pytest.mark.parametrize("text,mode", [
    ("create hello.txt", "direct"),
    ("fix the login form validation bug", "direct"),
    ("create a GET /users endpoint", "direct"),
    ("create a landing page with hero and pricing and faq", "direct"),
    ("implement JWT authentication system", "dag"),   # auth ⇒ backend+database
    ("build a full-stack web app with frontend backend database and tests", "dag"),
    ("build a full SaaS platform with auth payments dashboard docs and tests", "dag"),
])
def test_mode_selection(text, mode):
    p = optimize(text)
    assert p.mode == mode, (text, p.to_dict()["mode"], p.reasoning,
                            p.graph.total_nodes, p.complexity.level)
    assert p.reasoning                         # every decision is explained


def test_full_stack_orchestrates_not_inflated():
    p = optimize("build a full-stack web app with frontend backend database and tests")
    assert p.mode == "dag"
    # agents == domains, not per-file
    assert p.allocation.count == p.graph.total_nodes
    assert 3 <= p.allocation.count <= 6
    assert p.validation.valid


# ── Dependency graph engine ──────────────────────────────────────────────────

def test_graph_detects_chain_and_critical_path():
    g = DependencyGraph(["frontend", "backend", "database"])
    gm = g.metrics()
    assert not gm.has_cycle
    # database → backend → frontend is the critical chain
    assert gm.longest_path[0] == "database"
    assert gm.longest_path[-1] == "frontend"
    assert gm.total_nodes == 3


def test_graph_parallelism_low_for_chain():
    g = DependencyGraph(["frontend", "backend", "database"])
    w, ratio, cls = g.parallelism()
    assert w == 1                              # a pure chain has width 1
    assert cls in ("none", "very_low")


def test_graph_parallelism_high_for_independent():
    # Independent domains (documentation has no deps; multiple roots) widen the graph.
    g = DependencyGraph(["documentation", "infrastructure"])
    w, ratio, cls = g.parallelism()
    assert w == 2 and ratio == 1.0
    assert cls == "very_high"


def test_graph_cycle_guard_does_not_hang():
    g = DependencyGraph(["frontend", "backend"])
    # Inject an artificial cycle and ensure detection + no recursion blowup.
    g.deps["backend"] = ["frontend"]
    g.deps["frontend"] = ["backend"]
    assert g._has_cycle() is True
    g.metrics()   # must not hang


# ── Complexity engine (explainable) ──────────────────────────────────────────

def test_complexity_breakdown_explainable():
    domains, items = parse_work("build a full SaaS platform with auth and payments and tests")
    g = DependencyGraph(domains)
    c = analyze_complexity("build a full SaaS platform with auth and payments and tests",
                           domains, items, g)
    assert c.level == "high"
    assert c.factors and c.score == pytest.approx(min(100.0, sum(c.factors.values())), abs=0.1)
    assert c.components >= 3


def test_complexity_low_for_single_domain():
    domains, items = parse_work("build a landing page with hero and pricing")
    g = DependencyGraph(domains)
    c = analyze_complexity("build a landing page with hero and pricing", domains, items, g)
    assert c.level in ("low", "medium")
    assert c.components == 1


# ── Swarm decision: only when benefit positive ───────────────────────────────

def test_swarm_requires_positive_benefit_and_width():
    # Chained domains never become swarm (no real parallel branches).
    p = optimize("build a full-stack web app with frontend backend database")
    assert p.mode != "swarm"


def test_swarm_validation_rejects_unjustified():
    from agent.orchestration_optimizer import ExecutionPlan
    p = optimize("build a full SaaS platform with auth payments docs tests")
    # Force an unjustified swarm and confirm the validator rejects it.
    p.mode = "swarm"
    p.benefit.net_benefit_s = -10.0
    v = validate_plan(p)
    assert v.valid is False
    assert any("swarm" in e for e in v.errors)


# ── Agent allocation ─────────────────────────────────────────────────────────

def test_allocate_one_agent_per_domain():
    domains, items = parse_work("build frontend backend and database")
    alloc = allocate_agents(domains, items)
    assert {a["domain"] for a in alloc.agents} == set(domains)
    assert alloc.count == len(set(domains))


# ── ETA engine ───────────────────────────────────────────────────────────────

def test_eta_has_all_components():
    p = optimize("build a full-stack web app with frontend backend database tests")
    e = p.par_eta.to_dict()
    for k in ("execution_s", "context_load_s", "agent_startup_s", "coordination_s",
              "merge_s", "validation_s", "total_s"):
        assert k in e
    assert p.seq_eta.total_s > 0


def test_direct_mode_no_coordination_overhead():
    p = optimize("create a GET /users endpoint")
    assert p.mode == "direct"
    assert p.par_eta.coordination_s == 0.0
    assert p.par_eta.agent_startup_s == 0.0


# ── Cost model: marginal-agent efficiency ────────────────────────────────────

def test_cost_efficiency_reported():
    p = optimize("build a full SaaS platform with auth payments docs tests")
    assert p.cost.total_tokens > 0
    assert isinstance(p.cost.efficiency, float)


# ── Validation layer ─────────────────────────────────────────────────────────

def test_validation_rejects_agent_inflation():
    from agent.orchestration_optimizer import AgentAllocation
    p = optimize("build a full-stack web app with frontend backend database")
    # Inflate agents beyond domain nodes → must be rejected.
    p.allocation = AgentAllocation(agents=[{"domain": f"x{i}", "work_items": [], "files": 1}
                                           for i in range(12)])
    v = validate_plan(p)
    assert v.valid is False
    assert any("inflation" in e for e in v.errors)


def test_valid_plan_passes():
    p = optimize("build a full-stack web app with frontend backend database tests")
    assert p.validation.valid is True


# ── Reporting ────────────────────────────────────────────────────────────────

def test_report_is_explainable():
    out = render_report(optimize("build a full SaaS platform with auth payments docs tests"))
    for section in ("Complexity Analysis", "Dependency Analysis", "Parallelism Analysis",
                    "Execution Strategy", "Agent Allocation", "Swarm Benefit",
                    "Cost Model", "Validation"):
        assert section in out


def test_confidence_in_range():
    for t in ("create hello.txt", "build a full SaaS platform with auth payments"):
        p = optimize(t)
        assert 0.0 <= p.confidence <= 1.0
