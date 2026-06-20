"""Replan Controller — decides when and how to update the running Action Graph."""

from __future__ import annotations

import logging
from agent.executive.executive_state import ExecutiveDecision, ExecutiveState

logger = logging.getLogger(__name__)

_REPLAN_FAILURE_THRESHOLD = 2    # replan after this many consecutive failures
_REPLAN_BLOCKED_THRESHOLD = 3    # replan if N+ actions are blocked (pending with failed deps)


class ReplanController:
    """Decides whether the Action Graph needs dynamic restructuring."""

    def __init__(self) -> None:
        self._consecutive_failures = 0

    def record_action_result(self, success: bool) -> None:
        if success:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

    def assess(self, state: ExecutiveState) -> ExecutiveDecision:
        h = state.health

        # Consecutive failures suggest the current plan is broken
        if self._consecutive_failures >= _REPLAN_FAILURE_THRESHOLD:
            logger.info(
                "ReplanController: %d consecutive failures — REPLAN",
                self._consecutive_failures,
            )
            state.annotations["replan_trigger"] = (
                f"{self._consecutive_failures} consecutive failures"
            )
            self._consecutive_failures = 0
            return ExecutiveDecision.REPLAN

        # Many pending actions with zero running → blocked
        if h.pending >= _REPLAN_BLOCKED_THRESHOLD and h.running == 0 and h.failed > 0:
            logger.info(
                "ReplanController: %d actions blocked (pending=%d, running=0, failed=%d) — REPLAN",
                h.pending, h.pending, h.failed,
            )
            state.annotations["replan_trigger"] = (
                f"{h.pending} actions blocked; {h.failed} failures with no runners"
            )
            return ExecutiveDecision.REPLAN

        return ExecutiveDecision.CONTINUE
