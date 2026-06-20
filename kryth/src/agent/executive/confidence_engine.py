"""Confidence Engine — maintains a multi-dimensional confidence score.

Confidence dimensions:
  mission     — derived from health (progress + failure rate)
  planning    — derived from replan count and plan coverage
  execution   — derived from UAL action success rate
  verification — derived from verified / total ratio
  recovery    — derived from rollback success rate
  memory      — derived from memory hit rate
  executors   — derived from per-executor success rates

The weighted overall score drives intervention thresholds across all
other controllers.
"""

from __future__ import annotations

import logging
from agent.executive.executive_state import ConfidenceScore, ExecutiveState

logger = logging.getLogger(__name__)


class ConfidenceEngine:
    """Updates ConfidenceScore from observable metrics."""

    def update(
        self,
        state: ExecutiveState,
        ual_analytics: dict | None = None,
        planner_analytics: dict | None = None,
    ) -> ConfidenceScore:
        c = state.confidence
        h = state.health
        ua = ual_analytics or {}
        pa = planner_analytics or {}

        # Mission confidence — progress and failure rate
        if h.total_actions > 0:
            progress     = h.progress_pct / 100
            failure_pen  = h.failure_rate
            c.mission    = max(0.0, min(1.0, 0.5 + progress * 0.5 - failure_pen))

        # Planning confidence — penalise replanning
        replan_pen   = min(0.4, h.replan_count * 0.08)
        c.planning   = max(0.0, 1.0 - replan_pen)

        # Execution confidence — from UAL success rate
        ual_rate     = ua.get("success_rate_pct", 100.0) / 100
        c.execution  = max(0.0, min(1.0, ual_rate))

        # Verification confidence — from verified actions
        ver_rate     = ua.get("verification_rate_pct", 100.0) / 100
        c.verification = max(0.0, min(1.0, ver_rate))

        # Recovery confidence — memory hit rate and rollback history
        mem_rate     = ua.get("memory_hit_rate_pct", 50.0) / 100
        c.memory     = max(0.0, min(1.0, mem_rate))

        # Recovery: 1.0 unless there are escalations
        c.recovery   = max(0.0, 1.0 - len(state.escalations) * 0.10)

        # Executor confidence — minimum executor success rate
        exec_stats   = ua.get("executor_stats", {})
        if exec_stats:
            rates = [s.get("rate", 1.0) for s in exec_stats.values() if s.get("total", 0) >= 2]
            c.executors = min(rates) if rates else 1.0

        overall = c.overall
        logger.info(
            "ConfidenceEngine: overall=%.2f "
            "(mission=%.2f plan=%.2f exec=%.2f verif=%.2f)",
            overall, c.mission, c.planning, c.execution, c.verification,
        )
        return c
