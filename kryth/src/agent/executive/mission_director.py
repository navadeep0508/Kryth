"""Mission Director — monitors mission health and decides Continue/Pause/Abort."""

from __future__ import annotations

import logging
from agent.executive.executive_state import (
    ExecutiveDecision, MissionHealth, ExecutiveState,
)

logger = logging.getLogger(__name__)

_MAX_FAILURE_RATE   = 0.40   # abort if >40% of completed actions failed
_MAX_REPLAN_COUNT   = 5      # abort if replanned >5 times
_IDLE_THRESHOLD_PCT = 0.50   # warn if >50% workers idle


class MissionDirector:
    """Observes mission health and produces a Continue/Pause/Abort decision."""

    def assess(self, state: ExecutiveState) -> ExecutiveDecision:
        h = state.health

        # Abort on runaway failures
        if h.failure_rate > _MAX_FAILURE_RATE and h.total_actions >= 4:
            logger.warning(
                "MissionDirector: failure rate %.0f%% > threshold — ABORT",
                h.failure_rate * 100,
            )
            state.annotations["mission_abort_reason"] = (
                f"Failure rate {h.failure_rate:.0%} exceeds threshold {_MAX_FAILURE_RATE:.0%}"
            )
            return ExecutiveDecision.ABORT

        # Abort on too many replans
        if h.replan_count > _MAX_REPLAN_COUNT:
            logger.warning("MissionDirector: replan limit (%d) exceeded", _MAX_REPLAN_COUNT)
            state.annotations["mission_abort_reason"] = (
                f"Replan count {h.replan_count} exceeds limit {_MAX_REPLAN_COUNT}"
            )
            return ExecutiveDecision.ABORT

        # Pause if many workers idle (possible deadlock)
        if state.team.active_workers > 0:
            idle_ratio = state.team.idle_workers / state.team.active_workers
            if idle_ratio > _IDLE_THRESHOLD_PCT:
                logger.info(
                    "MissionDirector: %.0f%% workers idle — possible deadlock, PAUSE",
                    idle_ratio * 100,
                )
                state.annotations["mission_pause_reason"] = "High idle ratio — possible deadlock"
                return ExecutiveDecision.PAUSE

        # Replan if there are pending failures
        if h.failed > 0 and h.pending > 0:
            return ExecutiveDecision.REPLAN

        return ExecutiveDecision.CONTINUE
