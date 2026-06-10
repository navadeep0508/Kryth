"""Confidence Controller — decide autonomous vs. approval thresholds.

Maps a confidence score (0.0–1.0) to an action decision:

    >= 0.90  →  AUTONOMOUS   (execute without asking)
    >= 0.65  →  ASK_ONCE     (show plan, ask once)
    >= 0.40  →  REQUIRE_APPROVAL  (full approval gate)
    <  0.40  →  BLOCK        (do not execute; report uncertainty)

Thresholds are tunable per mission profile.
Never allows bypass of hard safety denies from profiles.py.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ConfidenceDecision(str, Enum):
    AUTONOMOUS = "autonomous"
    ASK_ONCE = "ask_once"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


@dataclass
class ConfidenceResult:
    decision: ConfidenceDecision
    confidence: float
    reason: str
    override_by_risk: bool = False   # True if risk engine forced a higher gate


_THRESHOLDS_DEFAULT = {
    ConfidenceDecision.AUTONOMOUS: 0.90,
    ConfidenceDecision.ASK_ONCE: 0.65,
    ConfidenceDecision.REQUIRE_APPROVAL: 0.40,
}


class ConfidenceController:
    """Map confidence scores to action decisions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thresholds = dict(_THRESHOLDS_DEFAULT)

    def configure(
        self,
        autonomous_threshold: float = 0.90,
        ask_once_threshold: float = 0.65,
        require_threshold: float = 0.40,
    ) -> None:
        with self._lock:
            self._thresholds[ConfidenceDecision.AUTONOMOUS] = autonomous_threshold
            self._thresholds[ConfidenceDecision.ASK_ONCE] = ask_once_threshold
            self._thresholds[ConfidenceDecision.REQUIRE_APPROVAL] = require_threshold

    def decide(
        self,
        confidence: float,
        risk_level: str = "low",
        action_description: str = "",
    ) -> ConfidenceResult:
        """Map confidence + risk to a decision.

        High risk always elevates the gate regardless of confidence.
        """
        with self._lock:
            thresholds = dict(self._thresholds)

        # Risk elevation: high/critical risk raises the bar by 2 tiers
        override_by_risk = False
        if risk_level in ("high", "critical"):
            # Force at minimum REQUIRE_APPROVAL
            if confidence >= thresholds[ConfidenceDecision.AUTONOMOUS]:
                decision = ConfidenceDecision.REQUIRE_APPROVAL
                override_by_risk = True
            elif confidence >= thresholds[ConfidenceDecision.ASK_ONCE]:
                decision = ConfidenceDecision.REQUIRE_APPROVAL
                override_by_risk = True
            elif confidence >= thresholds[ConfidenceDecision.REQUIRE_APPROVAL]:
                decision = ConfidenceDecision.REQUIRE_APPROVAL
            else:
                decision = ConfidenceDecision.BLOCK
        elif risk_level == "medium":
            # Medium risk: cap at ASK_ONCE even if confidence is high
            if confidence >= thresholds[ConfidenceDecision.AUTONOMOUS]:
                decision = ConfidenceDecision.ASK_ONCE
                override_by_risk = True
            elif confidence >= thresholds[ConfidenceDecision.ASK_ONCE]:
                decision = ConfidenceDecision.ASK_ONCE
            elif confidence >= thresholds[ConfidenceDecision.REQUIRE_APPROVAL]:
                decision = ConfidenceDecision.REQUIRE_APPROVAL
            else:
                decision = ConfidenceDecision.BLOCK
        else:
            # Low risk: pure confidence
            if confidence >= thresholds[ConfidenceDecision.AUTONOMOUS]:
                decision = ConfidenceDecision.AUTONOMOUS
            elif confidence >= thresholds[ConfidenceDecision.ASK_ONCE]:
                decision = ConfidenceDecision.ASK_ONCE
            elif confidence >= thresholds[ConfidenceDecision.REQUIRE_APPROVAL]:
                decision = ConfidenceDecision.REQUIRE_APPROVAL
            else:
                decision = ConfidenceDecision.BLOCK

        reason = self._reason(confidence, decision, risk_level, override_by_risk,
                               action_description)
        return ConfidenceResult(
            decision=decision,
            confidence=confidence,
            reason=reason,
            override_by_risk=override_by_risk,
        )

    def is_autonomous(self, confidence: float, risk_level: str = "low") -> bool:
        return self.decide(confidence, risk_level).decision == ConfidenceDecision.AUTONOMOUS

    def _reason(
        self,
        confidence: float,
        decision: ConfidenceDecision,
        risk_level: str,
        override: bool,
        desc: str,
    ) -> str:
        base = f"confidence {confidence:.0%}"
        if override:
            base += f" (elevated by {risk_level} risk)"
        action = desc[:60] + "…" if len(desc) > 60 else desc
        return f"{base} → {decision.value}" + (f" for: {action}" if action else "")


# Module-level singleton
confidence_controller = ConfidenceController()
