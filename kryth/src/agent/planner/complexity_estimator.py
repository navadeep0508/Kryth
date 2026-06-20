"""Complexity Estimator — quantifies effort before the LLM plans anything.

Produces a ComplexityEstimate from an Objective without calling the LLM,
so planning overhead is minimal for small tasks.
"""

from __future__ import annotations

from agent.planner.planner_state import Complexity, ComplexityEstimate, Objective

# Tuning constants
_ACTIONS_PER_DOMAIN  = {"small": 2, "medium": 4, "large": 7, "massive": 12}
_WORKERS_BY_COMPLEXITY = {
    Complexity.SMALL:   1,
    Complexity.MEDIUM:  3,   # lead + 2
    Complexity.LARGE:   6,   # lead + 5
    Complexity.MASSIVE: 10,  # dynamic
}
_TOKENS_PER_ACTION = 800
_SECONDS_PER_ACTION = 45


class ComplexityEstimator:

    def estimate(self, objective: Objective) -> ComplexityEstimate:
        complexity = objective.complexity_hint or Complexity.MEDIUM
        n_domains  = max(1, len(objective.domains))

        # Action count: domains × actions-per-complexity
        scale = complexity.value
        base  = _ACTIONS_PER_DOMAIN.get(scale, 4)
        action_count = max(1, n_domains * base)

        # Parallel layers: roughly sqrt(action count)
        import math
        parallel_layers = max(1, int(math.ceil(math.sqrt(action_count))))

        worker_count       = _WORKERS_BY_COMPLEXITY[complexity]
        estimated_tokens   = action_count * _TOKENS_PER_ACTION
        estimated_duration = action_count * _SECONDS_PER_ACTION / max(1, worker_count)

        risk_level = {
            Complexity.SMALL:   "low",
            Complexity.MEDIUM:  "medium",
            Complexity.LARGE:   "high",
            Complexity.MASSIVE: "critical",
        }[complexity]

        rationale = (
            f"{complexity.value.capitalize()} complexity: "
            f"{n_domains} domain(s) ({', '.join(objective.domains[:4])}), "
            f"~{action_count} actions across ~{parallel_layers} parallel layers, "
            f"~{worker_count} agents."
        )

        return ComplexityEstimate(
            complexity=complexity,
            action_count=action_count,
            worker_count=worker_count,
            parallel_layers=parallel_layers,
            estimated_tokens=estimated_tokens,
            estimated_duration_s=estimated_duration,
            risk_level=risk_level,
            domains=objective.domains,
            rationale=rationale,
        )
