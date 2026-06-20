"""Escalation Controller — decides when to surface a situation to the user.

Escalation triggers (in priority order):
  1. Security score critically low
  2. Budget exhausted
  3. Risk level CRITICAL
  4. Confidence overall < 0.30
  5. Three or more consecutive ABORT recommendations
  6. Rollback itself failed

Escalations are recorded in ExecutiveState.escalations so the dashboard
and executive_report can surface them.
"""

from __future__ import annotations

import logging
from agent.executive.executive_state import (
    ExecutiveDecision, ExecutiveState, RiskLevel,
)

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE_BEFORE_ESCALATE = 0.30
_CONSECUTIVE_ABORT_LIMIT        = 3


class EscalationController:

    def __init__(self) -> None:
        self._consecutive_aborts = 0

    def record_decision(self, decision: ExecutiveDecision) -> None:
        if decision == ExecutiveDecision.ABORT:
            self._consecutive_aborts += 1
        else:
            self._consecutive_aborts = 0

    def assess(self, state: ExecutiveState) -> ExecutiveDecision:
        reasons: list[str] = []

        if state.quality.security_score < 0.40:
            reasons.append(
                f"Security score critically low: {state.quality.security_score:.0%}"
            )

        if state.budget.over_budget:
            reasons.append(
                f"Token budget exhausted ({state.budget.tokens_used:,} / {state.budget.token_limit:,})"
            )

        if state.risk == RiskLevel.CRITICAL:
            reasons.append("Risk level CRITICAL")

        if state.confidence.overall < _MIN_CONFIDENCE_BEFORE_ESCALATE:
            reasons.append(
                f"Overall confidence too low: {state.confidence.overall:.0%}"
            )

        if self._consecutive_aborts >= _CONSECUTIVE_ABORT_LIMIT:
            reasons.append(
                f"{self._consecutive_aborts} consecutive abort signals"
            )

        if reasons:
            for r in reasons:
                if r not in state.escalations:
                    state.escalations.append(r)
                    logger.warning("ESCALATION: %s", r)
            return ExecutiveDecision.ESCALATE

        return ExecutiveDecision.CONTINUE
