"""Action Graph Builder — assembles a UAL ActionGraph from decomposed action dicts.

Enriches each action with:
  - preferred_executors  (from ExecutorPlanner)
  - verification_steps   (from VerificationPlanner)
  - rollback_steps       (from RollbackPlanner)
  - risk level           (from complexity estimate)
  - dependencies         (from DependencyBuilder)

Then constructs and validates a UAL ActionGraph ready for the scheduler.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.action.action import Action, ActionRisk
from agent.action.action_graph import ActionGraph
from agent.planner.dependency_builder import DependencyBuilder
from agent.planner.executor_planner import ExecutorPlanner
from agent.planner.planner_state import ComplexityEstimate, Objective
from agent.planner.rollback_planner import RollbackPlanner
from agent.planner.verification_planner import VerificationPlanner

logger = logging.getLogger(__name__)

_RISK_MAP = {
    "low":      ActionRisk.LOW,
    "medium":   ActionRisk.MEDIUM,
    "high":     ActionRisk.HIGH,
    "critical": ActionRisk.CRITICAL,
}


class ActionGraphBuilder:
    """Converts raw action dicts into a validated UAL ActionGraph."""

    def __init__(self) -> None:
        self._dep_builder   = DependencyBuilder()
        self._exec_planner  = ExecutorPlanner()
        self._verif_planner = VerificationPlanner()
        self._rb_planner    = RollbackPlanner()

    def build(
        self,
        raw_actions: list[dict[str, Any]],
        objective: Objective,
        estimate: ComplexityEstimate,
    ) -> ActionGraph:
        # 1. Fill in inferred dependencies
        raw_actions = self._dep_builder.build(raw_actions)

        # 2. Determine base risk from complexity
        base_risk = _RISK_MAP.get(estimate.risk_level, ActionRisk.MEDIUM)

        # 3. Build Action objects
        graph = ActionGraph(mission=objective.goal)
        for item in raw_actions:
            goal   = item.get("goal", item["id"])
            params = item.get("params", {})

            action = Action(
                id=item["id"],
                goal=goal,
                description=item.get("description", goal),
                priority=int(item.get("priority", 50)),
                dependencies=list(item.get("dependencies", [])),
                risk=base_risk,
                estimated_cost_tokens=int(item.get("estimated_cost_tokens", 800)),
                estimated_time_ms=float(item.get("estimated_time_ms", 0)),
                preferred_executors=item.get(
                    "preferred_executors",
                    self._exec_planner.assign(goal, params),
                ),
                verification_steps=item.get(
                    "verification_steps",
                    self._verif_planner.generate(goal, params),
                ),
                rollback_steps=item.get(
                    "rollback_steps",
                    self._rb_planner.generate(goal, params),
                ),
                affected_paths=list(item.get("affected_paths", [])),
                confidence=float(item.get("confidence", 1.0)),
                params=params,
            )
            graph.add(action)

        # 4. Validate
        errors = graph.validate()
        if errors:
            logger.warning("ActionGraph validation errors (auto-fixing): %s", errors)
            graph = self._auto_fix(graph, errors)

        logger.info(
            "Built ActionGraph: mission='%s' actions=%d layers=%d",
            objective.goal, len(graph.actions), len(graph.layers()),
        )
        return graph

    # ── Auto-fix ──────────────────────────────────────────────────────

    def _auto_fix(self, graph: ActionGraph, errors: list[str]) -> ActionGraph:
        """Remove unknown dependency references and try again."""
        valid_ids = set(graph.actions.keys())
        for action in graph.actions.values():
            action.dependencies = [d for d in action.dependencies if d in valid_ids]
        return graph
