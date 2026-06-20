"""Universal Planner — strategic brain of KRYTH.

Converts any natural-language objective into a validated UAL ActionGraph
through a 7-stage pipeline:

  ObjectiveParser       → Objective
  ComplexityEstimator   → ComplexityEstimate
  TeamPlanner           → TeamSpec
  DecompositionEngine   → raw action list
  ActionGraphBuilder    → UAL ActionGraph (with executors/verify/rollback)
  PlannerMemory         → record outcomes
  PlannerAnalytics      → benchmark metrics

Then hands the graph to ActionManager for execution, supporting dynamic
replanning on failure via Replanner.

Usage::

    from agent.planner import UniversalPlanner

    planner = UniversalPlanner()
    result  = planner.plan_and_run("Build JWT authentication for the API")
    print(result.success, result.succeeded, result.failed)

    # Or just produce the graph without running:
    graph = planner.plan("Build JWT authentication for the API")
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from agent.planner.planner_state import (
    Objective, ComplexityEstimate, TeamSpec, PlannerState, PlannerStatus,
)
from agent.planner.objective_parser    import ObjectiveParser
from agent.planner.complexity_estimator import ComplexityEstimator
from agent.planner.team_planner        import TeamPlanner
from agent.planner.decomposition_engine import DecompositionEngine
from agent.planner.action_graph_builder import ActionGraphBuilder
from agent.planner.replanner           import Replanner, ReplanStrategy
from agent.planner.planner_memory      import PlannerMemory, PlanRecord
from agent.planner.planner_analytics   import PlannerAnalytics, PlannerMetric
from agent.planner.planner_dashboard   import push_planner_event

from agent.action.action_graph  import ActionGraph
from agent.action.action_manager import ActionManager
from agent.action.action_scheduler import SchedulerResult

logger = logging.getLogger(__name__)


@dataclass
class PlanResult:
    """Combined planning + execution result."""
    success: bool
    session_id: str
    objective: str
    complexity: str
    action_count: int
    parallel_layers: int
    worker_estimate: int
    replan_count: int
    planning_latency_ms: float
    graph: Optional[ActionGraph] = None
    scheduler_result: Optional[SchedulerResult] = None
    error: str = ""


class UniversalPlanner:
    """The strategic brain of KRYTH — plan-first, execute-second."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root      = Path(project_root).resolve()
        self._parser    = ObjectiveParser()
        self._estimator = ComplexityEstimator()
        self._team_plr  = TeamPlanner()
        self._decomposer = DecompositionEngine()
        self._builder   = ActionGraphBuilder()
        self._replanner = Replanner()
        self._memory    = PlannerMemory(self._root)
        self._analytics = PlannerAnalytics(self._root)
        self._manager   = ActionManager(self._root)

    # ── Public API ────────────────────────────────────────────────────

    def plan(self, user_input: str, context: dict | None = None) -> ActionGraph:
        """Produce an ActionGraph from a natural-language objective.

        Does NOT execute — returns the graph for inspection or manual run.
        """
        state = self._make_state()
        obj, est, _ = self._pipeline_plan(state, user_input, context)
        return state.plan_dict["_graph"]

    def plan_and_run(
        self,
        user_input: str,
        context: dict | None = None,
        max_replans: int = 3,
    ) -> PlanResult:
        """Plan then execute, with dynamic replanning on failure."""
        session_id = str(uuid.uuid4())[:8]
        state      = self._make_state(session_id)

        push_planner_event("started", session_id=session_id, mission=user_input)
        t_start = time.time()

        # ── Plan ──────────────────────────────────────────────────────
        try:
            obj, est, graph = self._pipeline_plan(state, user_input, context)
        except Exception as exc:
            logger.error("Planning failed: %s", exc, exc_info=True)
            push_planner_event("completed", success=False)
            return PlanResult(
                success=False, session_id=session_id, objective=user_input,
                complexity="unknown", action_count=0, parallel_layers=0,
                worker_estimate=0, replan_count=0,
                planning_latency_ms=(time.time() - t_start) * 1000,
                error=str(exc),
            )

        planning_ms = (time.time() - t_start) * 1000
        push_planner_event(
            "graph_ready",
            action_count=len(graph.actions),
            parallel_layers=len(graph.layers()),
        )

        # ── Execute ───────────────────────────────────────────────────
        exec_start    = time.time()
        replan_count  = 0
        sched_result  = self._manager.run_graph(graph)

        # Dynamic replanning loop
        while not sched_result.success and replan_count < max_replans:
            failed = [
                a for a in graph.actions.values()
                if a.result and not a.result.success
            ]
            if not failed:
                break

            decision = self._replanner.replan(
                graph, failed[0], failed[0].result, replan_count
            )
            if decision.strategy == ReplanStrategy.ABORT:
                logger.warning("Replanner aborted: %s", decision.rationale)
                break

            push_planner_event("replanned", strategy=decision.strategy.value)
            self._replanner.apply(graph, decision)
            replan_count += 1
            sched_result = self._manager.run_graph(graph)

        actual_duration = time.time() - exec_start
        success = sched_result.success
        push_planner_event("completed", success=success)

        # ── Record ────────────────────────────────────────────────────
        executors_used = list({
            r.executor_used
            for r in sched_result.outputs.values()
            if r.executor_used
        })
        layers = len(graph.layers()) if not graph.has_failures else 0

        self._memory.record(PlanRecord(
            session_id=session_id,
            objective_goal=user_input,
            complexity=est.complexity.value,
            action_count=len(graph.actions),
            layer_count=layers,
            worker_count=est.worker_count,
            success=success,
            duration_s=actual_duration,
            replan_count=replan_count,
            executors_used=executors_used,
        ))
        self._analytics.record(PlannerMetric(
            session_id=session_id,
            objective=user_input,
            complexity=est.complexity.value,
            planning_latency_ms=planning_ms,
            action_count=len(graph.actions),
            dependency_count=sum(
                len(a.dependencies) for a in graph.actions.values()
            ),
            parallel_layers=layers,
            estimated_workers=est.worker_count,
            actual_workers=len(executors_used),
            estimated_duration_s=est.estimated_duration_s,
            actual_duration_s=actual_duration,
            replan_count=replan_count,
            mission_success=success,
        ))

        return PlanResult(
            success=success,
            session_id=session_id,
            objective=user_input,
            complexity=est.complexity.value,
            action_count=len(graph.actions),
            parallel_layers=layers,
            worker_estimate=est.worker_count,
            replan_count=replan_count,
            planning_latency_ms=planning_ms,
            graph=graph,
            scheduler_result=sched_result,
        )

    def analytics_summary(self) -> dict:
        return self._analytics.summary()

    def memory_stats(self) -> dict:
        return self._memory.stats()

    # ── Pipeline ──────────────────────────────────────────────────────

    def _pipeline_plan(
        self,
        state: PlannerState,
        user_input: str,
        context: dict | None,
    ) -> tuple[Objective, ComplexityEstimate, ActionGraph]:
        state.status = PlannerStatus.PLANNING

        # 1. Parse objective
        obj = self._parser.parse(user_input, context)
        state.objective = obj
        logger.info("Objective: %s | domains=%s", obj.goal, obj.domains)

        # 2. Estimate complexity
        est = self._estimator.estimate(obj)
        state.estimate = est
        push_planner_event(
            "complexity",
            complexity=est.complexity.value,
            workers=est.worker_count,
            estimated_duration_s=est.estimated_duration_s,
        )
        logger.info("Estimate: %s", est.rationale)

        # 3. Team composition (metadata only — scheduling is handled by ActionScheduler)
        team = self._team_plr.plan(est)
        state.team = team

        # 4. Decompose
        raw_actions = self._decomposer.decompose(obj)
        push_planner_event("decomposed", action_count=len(raw_actions))
        logger.info("Decomposed into %d actions", len(raw_actions))

        # 5. Build ActionGraph
        graph = self._builder.build(raw_actions, obj, est)
        state.plan_dict = {"_graph": graph}
        state.status    = PlannerStatus.READY

        return obj, est, graph

    def _make_state(self, session_id: str | None = None) -> PlannerState:
        return PlannerState(session_id=session_id or str(uuid.uuid4())[:8])
