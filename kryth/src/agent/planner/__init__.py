"""Universal Planner — public package exports.

The Universal Planner is the strategic brain of KRYTH.

Architecture::

    User Input
         |
    UniversalPlanner
         |
    ObjectiveParser      → Objective (goal, domains, requirements)
    ComplexityEstimator  → ComplexityEstimate (workers, layers, tokens)
    TeamPlanner          → TeamSpec (lead + workers + specialists)
    DecompositionEngine  → raw action list
    ActionGraphBuilder   → UAL ActionGraph (executors, verify, rollback)
         |
    ActionManager (UAL)
         |
    Executors → Verification → Memory → Analytics

Quick start::

    from agent.planner import UniversalPlanner

    planner = UniversalPlanner()
    result  = planner.plan_and_run("Build JWT authentication for the API")
    print(result.success, result.action_count, result.replan_count)
"""

from agent.planner.planner       import UniversalPlanner, PlanResult
from agent.planner.planner_state import (
    Objective, ComplexityEstimate, TeamSpec, PlannerState,
    Complexity, PlannerStatus,
)
from agent.planner.objective_parser     import ObjectiveParser
from agent.planner.complexity_estimator import ComplexityEstimator
from agent.planner.team_planner         import TeamPlanner
from agent.planner.decomposition_engine import DecompositionEngine
from agent.planner.action_graph_builder import ActionGraphBuilder
from agent.planner.dependency_builder   import DependencyBuilder
from agent.planner.executor_planner     import ExecutorPlanner
from agent.planner.verification_planner import VerificationPlanner
from agent.planner.rollback_planner     import RollbackPlanner
from agent.planner.replanner            import Replanner, ReplanDecision, ReplanStrategy
from agent.planner.planner_memory       import PlannerMemory, PlanRecord
from agent.planner.planner_analytics    import PlannerAnalytics, PlannerMetric
from agent.planner.planner_dashboard    import (
    push_planner_event, render_planner_panel, get_planner_state,
)

__all__ = [
    # Entry point
    "UniversalPlanner",
    "PlanResult",
    # State
    "Objective",
    "ComplexityEstimate",
    "TeamSpec",
    "PlannerState",
    "Complexity",
    "PlannerStatus",
    # Pipeline stages
    "ObjectiveParser",
    "ComplexityEstimator",
    "TeamPlanner",
    "DecompositionEngine",
    "ActionGraphBuilder",
    "DependencyBuilder",
    "ExecutorPlanner",
    "VerificationPlanner",
    "RollbackPlanner",
    # Replanning
    "Replanner",
    "ReplanDecision",
    "ReplanStrategy",
    # Memory + Analytics
    "PlannerMemory",
    "PlanRecord",
    "PlannerAnalytics",
    "PlannerMetric",
    # Dashboard
    "push_planner_event",
    "render_planner_panel",
    "get_planner_state",
]
