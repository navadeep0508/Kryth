"""Team Planner — decides team composition from a ComplexityEstimate.

Output:
  Small    → 1 generalist agent
  Medium   → Lead + 2 workers
  Large    → per-domain Lead + workers
  Massive  → full dynamic team with specialists
"""

from __future__ import annotations

from agent.planner.planner_state import (
    Complexity, ComplexityEstimate, TeamSpec,
)

# Specialist agents always added when the matching domain is present
_SPECIALIST_MAP: dict[str, str] = {
    "browser":    "browser",
    "devops":     "devops",
    "deployment": "devops",
    "security":   "security",
    "database":   "database",
}

# Domains that get a dedicated lead when the team is LARGE+
_LEAD_DOMAINS = {"backend", "frontend", "testing", "deployment", "devops"}


class TeamPlanner:

    def plan(self, estimate: ComplexityEstimate) -> TeamSpec:
        c = estimate.complexity

        if c == Complexity.SMALL:
            return TeamSpec(
                total_agents=1,
                lead_agents=[],
                worker_agents={"generalist": 1},
                specialist_agents=[],
                rationale="Single agent sufficient for small task.",
            )

        if c == Complexity.MEDIUM:
            workers = {d: 1 for d in estimate.domains[:3]}
            return TeamSpec(
                total_agents=1 + len(workers),
                lead_agents=["coordinator"],
                worker_agents=workers,
                specialist_agents=self._specialists(estimate.domains),
                rationale="Lead + workers for medium task.",
            )

        if c == Complexity.LARGE:
            leads   = [d for d in estimate.domains if d in _LEAD_DOMAINS][:3]
            workers = {d: 2 for d in estimate.domains}
            total   = len(leads) + sum(workers.values())
            return TeamSpec(
                total_agents=total,
                lead_agents=leads,
                worker_agents=workers,
                specialist_agents=self._specialists(estimate.domains),
                rationale=f"Per-domain leads ({leads}) + workers for large task.",
            )

        # MASSIVE
        leads   = [d for d in estimate.domains if d in _LEAD_DOMAINS]
        workers = {d: 3 for d in estimate.domains}
        specialists = list({
            "recovery", "documentation",
        } | set(self._specialists(estimate.domains)))
        total = len(leads) + sum(workers.values()) + len(specialists)
        return TeamSpec(
            total_agents=total,
            lead_agents=leads,
            worker_agents=workers,
            specialist_agents=specialists,
            rationale=(
                f"Full dynamic team: {len(leads)} leads, "
                f"{sum(workers.values())} workers, "
                f"{len(specialists)} specialists for massive task."
            ),
        )

    def _specialists(self, domains: list[str]) -> list[str]:
        seen = set()
        out  = []
        for d in domains:
            sp = _SPECIALIST_MAP.get(d)
            if sp and sp not in seen:
                seen.add(sp)
                out.append(sp)
        return out
