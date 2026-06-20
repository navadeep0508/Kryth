"""Executive Intelligence Layer — top-level supervisor.

The Executive NEVER executes tools, writes files, or bypasses the UAL.
It ONLY observes, decides, allocates, replans, and escalates.

Pipeline per review cycle:
  1. Ingest telemetry from UAL analytics + planner analytics
  2. Update ConfidenceEngine
  3. Run all controllers (mission, budget, quality, risk, team, executor, replan, escalation)
  4. Merge decisions (most severe wins)
  5. Record in ExecutiveMemory + ExecutiveAnalytics
  6. Update dashboard
  7. Return ExecutiveState to the caller

Usage::

    from agent.executive import Executive

    exec_ = Executive()

    # Called after each UAL scheduler tick:
    state = exec_.review(
        session_id="abc123",
        mission="Build SaaS",
        ual_analytics=manager.analytics_summary(),
        planner_analytics=planner.analytics_summary(),
        health_kwargs={"total_actions": 14, "succeeded": 8, "failed": 1,
                       "running": 3, "pending": 2, "skipped": 0, "replan_count": 0},
        budget_kwargs={"tokens": 12000, "llm_calls": 18, "elapsed_s": 45.0},
        quality_kwargs={"test_pass_rate": 0.92, "verification_rate": 0.88,
                        "security_score": 0.95, "eval_score": 0.85},
        team_kwargs={"active_workers": 4, "idle_workers": 1, "busy_workers": 3,
                     "parallel_efficiency_pct": 72.0},
    )

    print(state.recommendation.value)  # "continue"
    print(f"{state.confidence.overall:.0%}")  # "91%"
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from agent.executive.executive_state import (
    ExecutiveDecision, ExecutiveState, MissionHealth,
    BudgetState, QualityState, TeamState, ConfidenceScore, RiskLevel,
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
from agent.executive.executive_dashboard   import push_executive_state
from agent.executive.executive_report      import ExecutiveReport

logger = logging.getLogger(__name__)

# Decision severity order — highest index wins when merging
_SEVERITY: dict[ExecutiveDecision, int] = {
    ExecutiveDecision.CONTINUE: 0,
    ExecutiveDecision.SPAWN:    1,
    ExecutiveDecision.RETIRE:   1,
    ExecutiveDecision.REPLAN:   2,
    ExecutiveDecision.PAUSE:    3,
    ExecutiveDecision.ESCALATE: 4,
    ExecutiveDecision.ABORT:    5,
}


class Executive:
    """The autonomous engineering organization's executive supervisor."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root        = Path(project_root).resolve()
        self._mission_dir = MissionDirector()
        self._budget_ctl  = BudgetController()
        self._quality_ctl = QualityController()
        self._risk_ctl    = RiskController()
        self._team_ctl    = TeamController()
        self._exec_ctl    = ExecutorController()
        self._replan_ctl  = ReplanController()
        self._escalation_ctl = EscalationController()
        self._confidence  = ConfidenceEngine()
        self._memory      = ExecutiveMemory(self._root)
        self._analytics   = ExecutiveAnalytics(self._root)
        self._reporter    = ExecutiveReport(self._root)

        # Running state per session
        self._states: dict[str, ExecutiveState] = {}

    # ── Public API ────────────────────────────────────────────────────

    def review(
        self,
        session_id: str,
        mission: str,
        ual_analytics: dict | None = None,
        planner_analytics: dict | None = None,
        executor_stats: dict | None = None,
        health_kwargs: dict | None = None,
        budget_kwargs: dict | None = None,
        quality_kwargs: dict | None = None,
        team_kwargs: dict | None = None,
        action_succeeded: Optional[bool] = None,
    ) -> ExecutiveState:
        """Perform one review cycle and return the updated ExecutiveState."""
        state = self._get_or_create(session_id, mission)

        # 1. Ingest telemetry
        self._ingest(state, health_kwargs, budget_kwargs, quality_kwargs, team_kwargs)
        if budget_kwargs:
            self._budget_ctl.update(state, **budget_kwargs)
        if team_kwargs:
            self._team_ctl.update(state, **team_kwargs)
        if quality_kwargs:
            self._quality_ctl.update(state, **quality_kwargs)
        if action_succeeded is not None:
            self._replan_ctl.record_action_result(action_succeeded)

        # 2. Update confidence
        self._confidence.update(state, ual_analytics, planner_analytics)

        # 3. Run all controllers
        decisions: list[ExecutiveDecision] = [
            self._mission_dir.assess(state),
            self._budget_ctl.assess(state),
            self._quality_ctl.assess(state),
            self._risk_ctl.assess(state),
            self._team_ctl.assess(state),
            self._exec_ctl.assess(state, executor_stats),
            self._replan_ctl.assess(state),
            self._escalation_ctl.assess(state),
        ]

        # 4. Merge: most severe decision wins
        recommendation = max(decisions, key=lambda d: _SEVERITY[d])
        state.recommendation = recommendation
        state.rationale = self._build_rationale(state, decisions, recommendation)

        # Track decision history
        ts = datetime.now(timezone.utc).isoformat()[:19]
        state.decisions.append((ts, recommendation))
        self._escalation_ctl.record_decision(recommendation)

        # 5. Persist
        self._memory.record(ExecutiveRecord(
            session_id=session_id,
            mission=mission,
            decision=recommendation.value,
            rationale=state.rationale,
            confidence_overall=state.confidence.overall,
            risk_level=state.risk.value,
            success=recommendation in (ExecutiveDecision.CONTINUE, ExecutiveDecision.SPAWN,
                                       ExecutiveDecision.RETIRE),
            escalated=recommendation == ExecutiveDecision.ESCALATE,
            replan_count=state.health.replan_count,
            quality_overall=state.quality.overall,
            token_pct=state.budget.token_pct,
            elapsed_s=state.budget.elapsed_s,
        ))
        self._analytics.record(ExecutiveMetric(
            session_id=session_id,
            mission=mission,
            decision=recommendation.value,
            confidence_overall=state.confidence.overall,
            risk_level=state.risk.value,
            quality_overall=state.quality.overall,
            budget_pct=state.budget.token_pct,
            replan_count=state.health.replan_count,
            escalation_count=len(state.escalations),
            mission_success=recommendation not in (
                ExecutiveDecision.ABORT, ExecutiveDecision.ESCALATE
            ),
            elapsed_s=state.budget.elapsed_s,
        ))

        # 6. Update dashboard
        push_executive_state(state)

        logger.info(
            "Executive [%s]: %s | conf=%.2f risk=%s qual=%.0f%% budget=%.0f%%",
            session_id, recommendation.value,
            state.confidence.overall, state.risk.value,
            state.quality.overall * 100, state.budget.token_pct,
        )
        return state

    def finalize(self, session_id: str, elapsed_s: float = 0.0) -> str:
        """Write executive report for a completed session. Returns path."""
        state = self._states.get(session_id)
        if state is None:
            return ""
        return self._reporter.generate(state, elapsed_s)

    def analytics_summary(self) -> dict:
        return self._analytics.summary()

    def memory_stats(self) -> dict:
        return self._memory.stats()

    # ── Helpers ───────────────────────────────────────────────────────

    def _get_or_create(self, session_id: str, mission: str) -> ExecutiveState:
        if session_id not in self._states:
            self._states[session_id] = ExecutiveState(
                session_id=session_id, mission=mission
            )
        return self._states[session_id]

    def _ingest(
        self,
        state: ExecutiveState,
        health: dict | None,
        budget: dict | None,
        quality: dict | None,
        team: dict | None,
    ) -> None:
        if health:
            h = state.health
            h.total_actions     = health.get("total_actions",  h.total_actions)
            h.succeeded         = health.get("succeeded",       h.succeeded)
            h.failed            = health.get("failed",          h.failed)
            h.running           = health.get("running",         h.running)
            h.pending           = health.get("pending",         h.pending)
            h.skipped           = health.get("skipped",         h.skipped)
            h.replan_count      = health.get("replan_count",    h.replan_count)
            h.elapsed_s         = health.get("elapsed_s",       h.elapsed_s)
            h.estimated_total_s = health.get("estimated_total_s", h.estimated_total_s)

    def _build_rationale(
        self,
        state: ExecutiveState,
        decisions: list[ExecutiveDecision],
        final: ExecutiveDecision,
    ) -> str:
        counts = {}
        for d in decisions:
            counts[d.value] = counts.get(d.value, 0) + 1
        parts = [f"{v} x{n}" for v, n in sorted(counts.items())]
        conf  = f"conf={state.confidence.overall:.0%}"
        risk  = f"risk={state.risk.value}"
        qual  = f"qual={state.quality.overall:.0%}"
        return (
            f"Controllers: {', '.join(parts)} | "
            f"{conf} {risk} {qual} -> {final.value.upper()}"
        )
