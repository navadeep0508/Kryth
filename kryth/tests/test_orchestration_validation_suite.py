"""KRYTH Orchestration Engine Validation Suite — 20 cases + edge cases + score.

Each case asserts the graph-driven optimizer's mode + agent-allocation against
the specified expectation, then a scorer aggregates accuracy across dimensions.
This is the acceptance contract for the orchestration redesign.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.orchestration_optimizer import optimize


# (id, prompt, accepted_modes, max_agents)  — max_agents guards inflation.
CASES = [
    ("T1 tiny", "Change the color of a button from blue to green", {"direct"}, 1),
    ("T2 bug", "Fix login form validation bug", {"direct"}, 1),
    ("T3 api", "Create a GET /users endpoint", {"direct"}, 1),
    ("T4 feature", "Add dark mode support", {"direct", "dag"}, 2),
    ("T5 auth", "Implement JWT authentication system", {"dag"}, 4),
    ("T6 crud", "Create a task management dashboard with CRUD operations", {"dag"}, 4),
    ("T7 saas", "Build a SaaS platform with frontend backend PostgreSQL authentication and Stripe", {"dag"}, 7),
    ("T8 enterprise", "Build a Notion-scale enterprise platform", {"swarm"}, 8),
    ("T9 research", "Research 50 papers, generate citations, generate summaries, generate a final report", {"swarm"}, 8),
    ("T10 pages", "Create Home, About, Contact, Blog and Team pages", {"direct"}, 1),
    ("T11 sequential", "First create the database, then create the API, then connect the frontend", {"dag"}, 4),
    ("T12 parallel", "Generate 100 blogs, 100 tweets, 100 LinkedIn posts and 100 ad copies", {"swarm"}, 8),
    ("T13 fake", "Build a login page", {"direct", "dag"}, 3),
    ("T14 inflation", "Create a hero section, features section, pricing section and footer", {"direct"}, 1),
    ("T18 monorepo", "Refactor a 500k LOC monorepo", {"swarm"}, 10),
    ("T19 infra", "Set up Kubernetes, CI/CD, monitoring, logging and alerting", {"direct", "dag"}, 2),
    ("T20 factory", "Generate an idea, business plan, pitch deck, marketing strategy and financial forecast", {"swarm"}, 8),
]


@pytest.mark.parametrize("cid,prompt,modes,max_agents", CASES)
def test_validation_case(cid, prompt, modes, max_agents):
    p = optimize(prompt)
    assert p.mode in modes, (cid, p.mode, p.reasoning, p.graph.to_dict())
    assert p.allocation.count <= max_agents, (cid, "agent inflation", p.allocation.to_dict())
    # No plan may inflate agents beyond domain nodes.
    if p.mode != "direct":
        assert p.allocation.count == p.graph.total_nodes, (cid, "agents != nodes")
    assert p.validation.valid, (cid, p.validation.errors)


# ── Ambiguity (T16, T17) → clarification, no execution ───────────────────────

@pytest.mark.parametrize("prompt", ["Improve application", "Make it better"])
def test_ambiguous_requires_clarification(prompt):
    p = optimize(prompt)
    assert p.mode == "clarify"
    assert p.allocation.count == 0          # nothing spawned


# ── T14/T10 anti-inflation (explicit) ────────────────────────────────────────

def test_sections_and_pages_never_inflate():
    for prompt in ("Create a hero section, features section, pricing section and footer",
                   "Create Home, About, Contact, Blog and Team pages"):
        p = optimize(prompt)
        assert p.allocation.count == 1
        assert p.allocation.agents[0]["domain"] == "frontend"


# ── T15 / Edge F — dependency + cycle correctness ────────────────────────────

def test_cycle_detected_and_blocked():
    from agent.orchestration_optimizer import DependencyGraph, validate_plan, optimize as _opt
    g = DependencyGraph(["frontend", "backend"])
    g.deps["frontend"] = ["backend"]; g.deps["backend"] = ["frontend"]
    assert g._has_cycle() is True
    # A plan whose graph has a cycle must fail validation.
    p = _opt("build a full-stack web app with frontend backend database")
    p.graph.has_cycle = True
    v = validate_plan(p)
    assert v.valid is False and any("cycle" in e for e in v.errors)


def test_sequential_chain_low_parallelism():
    # Edge F: Database → Auth → Payments must be a chain, not parallel.
    p = optimize("First create the database, then create the API, then connect the frontend")
    assert p.graph.parallelism_class in ("none", "very_low", "low")
    assert p.mode == "dag"
    # critical path reflects the chain
    assert len(p.graph.longest_path) >= 2


# ── Edge B — small task, many keywords → no complexity inflation ──────────────

def test_fake_complexity_not_inflated():
    p = optimize("Build a login page")
    assert p.complexity.level in ("low", "medium")   # NOT high / 100
    assert p.complexity.score < 80


# ── Edge A — huge but sequential → DAG not SWARM ─────────────────────────────

def test_huge_sequential_is_dag_not_swarm():
    p = optimize("Build a full-stack web app with frontend backend database and tests")
    assert p.mode == "dag"


# ── Scoring: aggregate accuracy across dimensions ────────────────────────────

def test_orchestration_quality_score():
    """Aggregate accuracy; the suite must score >= 90/100 overall."""
    mode_hits = sum(1 for cid, pr, modes, mx in CASES if optimize(pr).mode in modes)
    alloc_hits = sum(1 for cid, pr, modes, mx in CASES if optimize(pr).allocation.count <= mx)
    valid_hits = sum(1 for cid, pr, modes, mx in CASES if optimize(pr).validation.valid)
    n = len(CASES)
    mode_acc = 100 * mode_hits / n
    alloc_acc = 100 * alloc_hits / n
    valid_acc = 100 * valid_hits / n
    overall = round((mode_acc + alloc_acc + valid_acc) / 3, 1)
    assert mode_acc >= 90, f"mode accuracy {mode_acc}"
    assert alloc_acc >= 95, f"allocation accuracy {alloc_acc}"
    assert overall >= 90, f"overall {overall}"
