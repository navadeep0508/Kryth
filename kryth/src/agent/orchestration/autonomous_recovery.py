"""AutonomousRecoveryV2 — V5 Phase 10.

Upgrades V4's milestone-level recovery (resume from checkpoint after a crash)
with INTELLIGENT recovery — the system reacts to *why* something failed:

  Provider failure     → switch provider, retry same milestone
  Worker failure        → reassign worker (fresh attempt, same module)
  Dependency failure     → replan milestone (via DynamicReplanner)
  Validation failure     → request rework (bounded retries)

Built on DynamicReplanner's failure classification — this module adds the
DECISION layer: given a classified failure, which concrete recovery ACTION
runs before the next attempt. Bounded by MAX_ATTEMPTS_PER_TARGET so a
mission escalates instead of looping forever on the same broken milestone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from agent.orchestration.dynamic_replanner import ReplanTrigger, classify_trigger


class RecoveryAction(Enum):
    SWITCH_PROVIDER   = "switch_provider"
    REASSIGN_WORKER   = "reassign_worker"
    REPLAN_MILESTONE  = "replan_milestone"
    REQUEST_REWORK    = "request_rework"
    ESCALATE          = "escalate"


_TRIGGER_TO_ACTION: Dict[ReplanTrigger, RecoveryAction] = {
    ReplanTrigger.PROVIDER_FAILURE:   RecoveryAction.SWITCH_PROVIDER,
    ReplanTrigger.DEPENDENCY_FAILURE: RecoveryAction.REPLAN_MILESTONE,
    ReplanTrigger.MILESTONE_FAILURE:  RecoveryAction.REASSIGN_WORKER,
    ReplanTrigger.CONTRACT_FAILURE:   RecoveryAction.REQUEST_REWORK,
    ReplanTrigger.TEST_FAILURE:       RecoveryAction.REQUEST_REWORK,
    ReplanTrigger.DEPLOYMENT_FAILURE: RecoveryAction.REPLAN_MILESTONE,
    ReplanTrigger.USER_SCOPE_CHANGE:  RecoveryAction.REPLAN_MILESTONE,
}

MAX_ATTEMPTS_PER_TARGET = 3


@dataclass
class RecoveryDecision:
    action: RecoveryAction
    trigger: ReplanTrigger
    target: str                  # milestone or module name
    notes: str = ""
    attempt: int = 1


@dataclass
class RecoveryOutcome:
    decision: RecoveryDecision
    success: bool
    notes: str = ""
    elapsed_s: float = 0.0


class AutonomousRecoveryEngine:
    """Decides + tracks recovery actions across a mission.

    Keeps the mission progressing instead of failing outright — escalates
    only after MAX_ATTEMPTS_PER_TARGET repeated failures on the same target.
    """

    def __init__(self) -> None:
        self._attempts: Dict[str, int] = {}
        self._history: List[RecoveryOutcome] = []
        self._provider_blacklist: List[str] = []

    def decide(
        self,
        error: str,
        target: str,
        context: str = "",
    ) -> RecoveryDecision:
        trigger = classify_trigger(error, context)
        attempt = self._attempts.get(target, 0) + 1
        self._attempts[target] = attempt

        if attempt > MAX_ATTEMPTS_PER_TARGET:
            return RecoveryDecision(
                action=RecoveryAction.ESCALATE,
                trigger=trigger,
                target=target,
                notes=f"Exceeded {MAX_ATTEMPTS_PER_TARGET} recovery attempts for '{target}'",
                attempt=attempt,
            )

        action = _TRIGGER_TO_ACTION.get(trigger, RecoveryAction.REASSIGN_WORKER)
        return RecoveryDecision(
            action=action,
            trigger=trigger,
            target=target,
            notes=f"{trigger.value} on '{target}' (attempt {attempt}) → {action.value}",
            attempt=attempt,
        )

    def record_outcome(
        self,
        decision: RecoveryDecision,
        success: bool,
        notes: str = "",
        elapsed_s: float = 0.0,
    ) -> RecoveryOutcome:
        outcome = RecoveryOutcome(decision=decision, success=success, notes=notes, elapsed_s=elapsed_s)
        self._history.append(outcome)
        if success:
            # Successful recovery resets the budget — a target that recovers
            # cleanly shouldn't carry forward a worse attempt count.
            self._attempts.pop(decision.target, None)
        return outcome

    def blacklist_provider(self, provider: str) -> None:
        if provider not in self._provider_blacklist:
            self._provider_blacklist.append(provider)

    def next_provider(self, available: List[str]) -> Optional[str]:
        candidates = [p for p in available if p not in self._provider_blacklist]
        return candidates[0] if candidates else None

    def history(self) -> List[RecoveryOutcome]:
        return list(self._history)

    def success_rate(self) -> float:
        if not self._history:
            return 1.0
        return sum(1 for o in self._history if o.success) / len(self._history)

    def summary(self) -> str:
        total = len(self._history)
        ok = sum(1 for o in self._history if o.success)
        return f"AutonomousRecovery: {ok}/{total} recoveries succeeded ({self.success_rate():.0%})"
