"""Cost & Risk Optimizer — decides whether parallel execution is worthwhile.

Only recommends parallel when the benefit (faster completion, isolated
ownership) outweighs the costs (context transfer, merge overhead,
coordination, startup).
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.orchestration.task_dag import TaskDAG
from agent.orchestration.team_generator import TeamPlan


@dataclass
class CostBenefitAnalysis:
    strategy: str              # "single", "sequential", "parallel"
    estimated_turns: int
    estimated_tokens: int
    estimated_seconds: int     # approximate wall clock
    total_agents: int
    parallelism_ratio: float   # what % of work can run in parallel (0-1)
    token_overhead: float      # extra tokens vs single agent
    risk_score: float          # 0-1
    rollback_available: bool
    recommendation: str        # one-line for the user

    def summary(self) -> str:
        pct = self.parallelism_ratio * 100
        overhead = self.token_overhead * 100
        return (
            f"Strategy: {self.strategy} | "
            f"~{self.estimated_turns} turns | "
            f"{self.estimated_seconds}s est. | "
            f"{self.total_agents} agent(s) | "
            f"parallelism: {pct:.0f}% | "
            f"overhead: +{overhead:.0f}% | "
            f"recommendation: {self.recommendation}"
        )


def analyze(
    dag: TaskDAG,
    team: TeamPlan,
) -> CostBenefitAnalysis:
    """Analyze costs and benefits and return a recommendation."""
    sequential_turns = dag.total_estimated_turns()
    token_cost = sequential_turns * 2000
    num_agents = len(team.agents)

    # Single agent
    if num_agents <= 1:
        return CostBenefitAnalysis(
            strategy="single",
            estimated_turns=sequential_turns,
            estimated_tokens=token_cost,
            estimated_seconds=sequential_turns * 30,
            total_agents=1,
            parallelism_ratio=0.0,
            token_overhead=0.0,
            risk_score=0.1,
            rollback_available=True,
            recommendation="Single agent — most efficient for this scope",
        )

    layers = dag.layers()

    # Sequential (respecting DAG dependencies)
    if team.recommended_strategy == "sequential":
        return CostBenefitAnalysis(
            strategy="sequential",
            estimated_turns=sequential_turns,
            estimated_tokens=token_cost,
            estimated_seconds=sequential_turns * 30,
            total_agents=num_agents,
            parallelism_ratio=0.0,
            token_overhead=0.2,  # context transfer
            risk_score=0.2,
            rollback_available=True,
            recommendation=f"Sequential with {num_agents} agents — dependencies require ordering",
        )

    # Parallel
    critical_path_turns = sum(
        max((dag.nodes[n.id].estimated_turns for n in layer), default=0)
        for layer in layers
    )
    parallel_turns = critical_path_turns + int(sequential_turns * 0.15)  # merge overhead
    parallelism_ratio = 1.0 - (parallel_turns / max(sequential_turns, 1))

    # Tokens: each agent duplicates some context
    token_overhead = 0.3 * num_agents

    # Risk: more agents = more coordination risk
    risk_score = min(1.0, 0.1 + 0.08 * num_agents)

    return CostBenefitAnalysis(
        strategy="parallel",
        estimated_turns=parallel_turns,
        estimated_tokens=int(token_cost * (1.0 + token_overhead)),
        estimated_seconds=critical_path_turns * 30,
        total_agents=num_agents,
        parallelism_ratio=max(0, parallelism_ratio),
        token_overhead=token_overhead,
        risk_score=risk_score,
        rollback_available=True,
        recommendation=f"Parallel with {num_agents} agents — "
        f"{parallelism_ratio:.0%} parallelism, +{token_overhead:.0%} token overhead",
    )
