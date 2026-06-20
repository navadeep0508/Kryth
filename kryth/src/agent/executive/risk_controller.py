"""Risk Controller — estimates execution, rollback, deployment, and data-loss risk."""

from __future__ import annotations

import logging
from agent.executive.executive_state import (
    ExecutiveDecision, ExecutiveState, RiskLevel,
)

logger = logging.getLogger(__name__)

# Risk factors: (annotation_key, score_contribution)
_ABORT_RISK_SCORE  = 80
_ESCALATE_RISK_SCORE = 60
_PAUSE_RISK_SCORE  = 40


class RiskController:
    """Aggregate risk signals and produce a risk level + decision."""

    def assess(self, state: ExecutiveState) -> ExecutiveDecision:
        score = self._compute_score(state)
        state.risk = self._score_to_level(score)
        state.annotations["risk_score"] = score

        logger.info("RiskController: score=%d level=%s", score, state.risk.value)

        if score >= _ABORT_RISK_SCORE:
            return ExecutiveDecision.ABORT
        if score >= _ESCALATE_RISK_SCORE:
            return ExecutiveDecision.ESCALATE
        if score >= _PAUSE_RISK_SCORE:
            return ExecutiveDecision.PAUSE
        return ExecutiveDecision.CONTINUE

    # ── Risk factors ──────────────────────────────────────────────────

    def _compute_score(self, state: ExecutiveState) -> int:
        score = 0

        # High failure rate
        if state.health.failure_rate >= 0.40:
            score += 30
        elif state.health.failure_rate >= 0.20:
            score += 15

        # Quality degraded
        if state.quality.overall < 0.50:
            score += 25
        elif state.quality.overall < 0.70:
            score += 10

        # Security breach
        if state.quality.security_score < 0.60:
            score += 30

        # Low confidence
        if state.confidence.overall < 0.40:
            score += 20
        elif state.confidence.overall < 0.60:
            score += 10

        # Budget overrun
        if state.budget.over_budget:
            score += 15
        elif state.budget.token_pct >= 90:
            score += 8

        # Excessive replanning
        if state.health.replan_count >= 5:
            score += 15
        elif state.health.replan_count >= 3:
            score += 7

        # Prior escalations
        score += len(state.escalations) * 5

        return score

    def _score_to_level(self, score: int) -> RiskLevel:
        if score >= _ABORT_RISK_SCORE:
            return RiskLevel.CRITICAL
        if score >= _ESCALATE_RISK_SCORE:
            return RiskLevel.HIGH
        if score >= _PAUSE_RISK_SCORE:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW
