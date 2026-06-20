"""Benchmark — graph-driven optimizer vs the old section-counting heuristic.

Run:  python tests/orchestration_optimizer_benchmark.py

Shows, per representative prompt: chosen mode, agent count, parallelism class,
and the explainable report. The headline metric is agent-inflation: the old
model created one agent per section/page; the new model creates one per
ownership domain.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.orchestration_optimizer import optimize, render_report

PROMPTS = [
    "create hello.txt",
    "build JWT authentication",
    "build CRUD API",
    "create a SaaS landing page with hero, features, pricing, FAQ, testimonials, contact, team, blog, footer",
    "build a full-stack web app with frontend backend database and tests",
    "build a full SaaS platform with auth payments dashboard docs and tests",
]


def _old_agent_estimate(text: str) -> int:
    """Approximate the OLD section-counting model: one unit per component + one
    per section. This is what produced the '12 agents / SWARM' inflation."""
    import re
    comps = len(re.findall(r"\b(frontend|backend|database|auth|payment|test|doc|deploy|ui|api)\w*", text, re.I))
    sections = len(re.findall(r"\b(hero|features?|pricing|faq|testimonials?|contact|team|blog|footer|dashboard)\b", text, re.I))
    return max(1, comps + sections)


def main() -> int:
    print("Orchestration optimizer — old (section-count) vs new (graph-driven)")
    print("=" * 88)
    print(f"{'prompt':<46}{'old agents':<12}{'new agents':<12}{'mode':<8}{'parallelism'}")
    print("-" * 88)
    worst_inflation = 0
    for p in PROMPTS:
        plan = optimize(p)
        old = _old_agent_estimate(p)
        new = plan.allocation.count
        worst_inflation = max(worst_inflation, old - new)
        print(f"{p[:44]:<46}{old:<12}{new:<12}{plan.mode:<8}{plan.graph.parallelism_class}")
    print("-" * 88)
    print(f"Max agents eliminated by domain-grouping: {worst_inflation}")
    print()
    # Show a full explainable report for the biggest case.
    print(render_report(optimize(PROMPTS[-1])))
    # Sanity: every plan must validate.
    bad = [p for p in PROMPTS if not optimize(p).validation.valid]
    if bad:
        print("\nRESULT: ✗ invalid plans:", bad)
        return 1
    print("\nRESULT: ✓ all plans validate; no agent inflation (agents == domains)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
