"""Executive Intelligence Layer — public package exports.

The EIL supervises the entire KRYTH engineering organisation.
It NEVER executes actions — it only observes, decides, and coordinates.

Full system flow::

    User Goal
         |
    Executive.review()          ← supervision loop
         |
    UniversalPlanner            ← strategic brain
         |
    ActionGraph
         |
    ActionManager (UAL)         ← execution kernel
         |
    Executors → Verify → Memory → Analytics
         |
    Executive.review()          ← post-tick assessment
         |
    Executive Report / Dashboard

Quick start::

    from agent.executive import Executive

    exec_ = Executive()
    state = exec_.review(
        session_id="s1",
        mission="Build SaaS",
        health_kwargs={"total_actions": 14, "succeeded": 10, "failed": 0,
                       "running": 2, "pending": 2, "skipped": 0, "replan_count": 0},
        budget_kwargs={"tokens": 45000, "llm_calls": 30, "elapsed_s": 120.0},
        quality_kwargs={"test_pass_rate": 0.95, "verification_rate": 0.90,
                        "security_score": 1.0, "eval_score": 0.88},
        team_kwargs={"active_workers": 4, "idle_workers": 1, "busy_workers": 3,
                     "parallel_efficiency_pct": 72.0},
    )
    print(state.recommendation.value)     # "continue"
    print(f"{state.confidence.overall:.0%}")  # "94%"

    report_path = exec_.finalize("s1", elapsed_s=180.0)
"""

from agent.executive.executive       import Executive
from agent.executive.executive_state import (
    ExecutiveState, ExecutiveDecision, RiskLevel,
    ConfidenceScore, BudgetState, QualityState, TeamState, MissionHealth,
)
from agent.executive.mission_director      import MissionDirector
from agent.executive.budget_controller     import BudgetController
from agent.executive.quality_controller    import QualityController
from agent.executive.risk_controller       import RiskController
from agent.executive.team_controller       import TeamController
from agent.executive.executor_controller   import ExecutorController
from agent.executive.replan_controller     import ReplanController
from agent.executive.escalation_controller import EscalationController
from agent.executive.confidence_engine     import ConfidenceEngine
from agent.executive.executive_memory      import ExecutiveMemory, ExecutiveRecord
from agent.executive.executive_analytics   import ExecutiveAnalytics, ExecutiveMetric
from agent.executive.executive_report      import ExecutiveReport
from agent.executive.executive_dashboard   import (
    push_executive_state, render_executive_panel, get_executive_state,
)

__all__ = [
    # Entry point
    "Executive",
    # State
    "ExecutiveState",
    "ExecutiveDecision",
    "RiskLevel",
    "ConfidenceScore",
    "BudgetState",
    "QualityState",
    "TeamState",
    "MissionHealth",
    # Controllers
    "MissionDirector",
    "BudgetController",
    "QualityController",
    "RiskController",
    "TeamController",
    "ExecutorController",
    "ReplanController",
    "EscalationController",
    # Intelligence
    "ConfidenceEngine",
    # Memory + Analytics
    "ExecutiveMemory",
    "ExecutiveRecord",
    "ExecutiveAnalytics",
    "ExecutiveMetric",
    # Report
    "ExecutiveReport",
    # Dashboard
    "push_executive_state",
    "render_executive_panel",
    "get_executive_state",
]
