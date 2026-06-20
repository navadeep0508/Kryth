"""Quality Controller — monitors test pass rate, verification, and eval scores."""

from __future__ import annotations

import logging
from agent.executive.executive_state import (
    ExecutiveDecision, ExecutiveState, QualityState,
)

logger = logging.getLogger(__name__)

_MIN_TEST_PASS_RATE    = 0.70
_MIN_VERIFICATION_RATE = 0.60
_MIN_SECURITY_SCORE    = 0.80
_MIN_EVAL_SCORE        = 0.65
_MIN_OVERALL_QUALITY   = 0.70


class QualityController:
    """Assesses overall quality; blocks deployment if thresholds are breached."""

    def assess(self, state: ExecutiveState) -> ExecutiveDecision:
        q = state.quality
        violations: list[str] = []

        if q.test_pass_rate < _MIN_TEST_PASS_RATE:
            violations.append(
                f"test_pass_rate {q.test_pass_rate:.0%} < {_MIN_TEST_PASS_RATE:.0%}"
            )
        if q.verification_rate < _MIN_VERIFICATION_RATE:
            violations.append(
                f"verification_rate {q.verification_rate:.0%} < {_MIN_VERIFICATION_RATE:.0%}"
            )
        if q.security_score < _MIN_SECURITY_SCORE:
            violations.append(
                f"security_score {q.security_score:.0%} < {_MIN_SECURITY_SCORE:.0%}"
            )
        if q.eval_score < _MIN_EVAL_SCORE:
            violations.append(
                f"eval_score {q.eval_score:.0%} < {_MIN_EVAL_SCORE:.0%}"
            )
        if q.overall < _MIN_OVERALL_QUALITY:
            violations.append(
                f"overall_quality {q.overall:.0%} < {_MIN_OVERALL_QUALITY:.0%}"
            )

        if violations:
            logger.warning("QualityController violations: %s", violations)
            state.annotations["quality_violations"] = violations
            # Security breach → escalate; other → pause for remediation
            if any("security" in v for v in violations):
                return ExecutiveDecision.ESCALATE
            return ExecutiveDecision.PAUSE

        return ExecutiveDecision.CONTINUE

    def update(self, state: ExecutiveState, **kwargs) -> None:
        """Ingest quality metrics from the UAL verifier or eval runner."""
        q = state.quality
        if "test_pass_rate" in kwargs:
            q.test_pass_rate = float(kwargs["test_pass_rate"])
        if "verification_rate" in kwargs:
            q.verification_rate = float(kwargs["verification_rate"])
        if "security_score" in kwargs:
            q.security_score = float(kwargs["security_score"])
        if "eval_score" in kwargs:
            q.eval_score = float(kwargs["eval_score"])
