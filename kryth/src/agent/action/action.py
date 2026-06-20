"""Core Action object — the atomic unit of the Universal Action Layer.

Everything KRYTH does is expressed as an Action. The LLM never thinks
"call terminal tool"; it thinks "achieve goal: build backend" and the
UAL routes it to the best executor automatically.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ActionStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    SUCCEEDED = "succeeded"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    ROLLED_BACK = "rolled_back"


class ActionRisk(str, Enum):
    LOW      = "low"       # read-only, reversible
    MEDIUM   = "medium"    # writes files, reversible
    HIGH     = "high"      # network, git push, deploys
    CRITICAL = "critical"  # destructive, irreversible


@dataclass
class ActionResult:
    """Outcome of executing one Action."""
    success: bool
    output: str = ""
    error: str = ""
    executor_used: str = ""
    duration_ms: float = 0.0
    verified: bool = False
    verification_detail: str = ""
    rolled_back: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Action:
    """The atomic unit of the Universal Action Layer.

    An Action describes WHAT to achieve (goal) without prescribing HOW.
    The executor_selector chooses the best executor at runtime.
    """
    # Identity
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    goal: str = ""
    description: str = ""

    # Scheduling
    priority: int = 50                         # 0 (highest) … 100 (lowest)
    dependencies: list[str] = field(default_factory=list)   # action ids

    # Risk / cost model
    risk: ActionRisk = ActionRisk.LOW
    estimated_cost_tokens: int = 0
    estimated_time_ms: float = 0.0

    # Requirements that must be satisfied before this action can run
    requirements: list[str] = field(default_factory=list)

    # Executor hints — preferred executors in order; empty = auto-select
    preferred_executors: list[str] = field(default_factory=list)

    # Verification: commands/checks to confirm success
    verification_steps: list[str] = field(default_factory=list)

    # Rollback: commands/changes to undo this action on failure
    rollback_steps: list[str] = field(default_factory=list)

    # Ownership — which files/dirs this action operates on
    affected_paths: list[str] = field(default_factory=list)

    # Runtime state
    status: ActionStatus = ActionStatus.PENDING
    owner: str = ""            # executor id that claimed this action
    confidence: float = 1.0    # 0–1; reduced by historical failures
    result: Optional[ActionResult] = None

    # Arbitrary context passed from the planner to the executor
    params: dict[str, Any] = field(default_factory=dict)

    def is_ready(self, completed_ids: set[str]) -> bool:
        """True when all dependencies have completed successfully."""
        return all(d in completed_ids for d in self.dependencies)

    def mark_running(self, executor_id: str) -> None:
        self.status = ActionStatus.RUNNING
        self.owner = executor_id

    def mark_succeeded(self, result: ActionResult) -> None:
        self.status = ActionStatus.SUCCEEDED
        self.result = result

    def mark_failed(self, result: ActionResult) -> None:
        self.status = ActionStatus.FAILED
        self.result = result

    def mark_rolled_back(self) -> None:
        self.status = ActionStatus.ROLLED_BACK
        if self.result:
            self.result.rolled_back = True

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value,
            "risk": self.risk.value,
            "priority": self.priority,
            "owner": self.owner,
            "confidence": self.confidence,
            "dependencies": self.dependencies,
            "preferred_executors": self.preferred_executors,
            "affected_paths": self.affected_paths,
            "result": {
                "success": self.result.success,
                "executor_used": self.result.executor_used,
                "duration_ms": self.result.duration_ms,
                "verified": self.result.verified,
            } if self.result else None,
        }
