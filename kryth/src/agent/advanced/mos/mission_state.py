"""Mission Operating System — mission state definitions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class MissionStatus(str, Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    PAUSED    = "paused"
    BLOCKED   = "blocked"
    COMPLETED = "completed"
    FAILED    = "failed"
    ARCHIVED  = "archived"


class MissionPriorityLevel(str, Enum):
    CRITICAL = "critical"  # 100
    HIGH     = "high"      # 75
    NORMAL   = "normal"    # 50
    LOW      = "low"       # 25
    IDLE     = "idle"      # 10


_PRIORITY_WEIGHTS: dict[MissionPriorityLevel, int] = {
    MissionPriorityLevel.CRITICAL: 100,
    MissionPriorityLevel.HIGH:     75,
    MissionPriorityLevel.NORMAL:   50,
    MissionPriorityLevel.LOW:      25,
    MissionPriorityLevel.IDLE:     10,
}


def priority_weight(level: MissionPriorityLevel) -> int:
    return _PRIORITY_WEIGHTS[level]


@dataclass
class ResourceBudget:
    """Resource budget allocated to a single mission."""
    max_workers:        int   = 4
    max_executors:      int   = 2
    max_browser_tabs:   int   = 1
    max_api_calls:      int   = 500
    max_tokens:         int   = 200_000
    max_memory_mb:      int   = 512

    # Consumed
    workers_used:       int   = 0
    executors_used:     int   = 0
    browser_tabs_used:  int   = 0
    api_calls_used:     int   = 0
    tokens_used:        int   = 0
    memory_mb_used:     int   = 0

    @property
    def worker_pct(self) -> float:
        return self.workers_used / self.max_workers * 100 if self.max_workers else 0.0

    @property
    def token_pct(self) -> float:
        return self.tokens_used / self.max_tokens * 100 if self.max_tokens else 0.0

    @property
    def api_pct(self) -> float:
        return self.api_calls_used / self.max_api_calls * 100 if self.max_api_calls else 0.0


@dataclass
class MissionMetrics:
    """Runtime metrics for a mission."""
    actions_total:   int   = 0
    actions_done:    int   = 0
    actions_failed:  int   = 0
    replan_count:    int   = 0
    escalation_count: int  = 0
    test_pass_rate:  float = 0.0
    verification_rate: float = 0.0
    confidence:      float = 1.0
    risk_level:      str   = "low"

    @property
    def progress_pct(self) -> float:
        return self.actions_done / self.actions_total * 100 if self.actions_total else 0.0


@dataclass
class Mission:
    """A single autonomous mission managed by the MOS."""

    # Identity
    id:           str  = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name:         str  = ""
    description:  str  = ""
    goal:         str  = ""

    # Scheduling
    priority:     MissionPriorityLevel = MissionPriorityLevel.NORMAL
    priority_score: int = 50          # computed, overrides enum for fine-grained ordering
    tags:         list[str] = field(default_factory=list)

    # State
    status:       MissionStatus = MissionStatus.QUEUED
    status_reason: str = ""

    # Dependencies
    depends_on:   list[str] = field(default_factory=list)   # mission ids
    blocks:       list[str] = field(default_factory=list)    # mission ids blocked by this

    # Resources
    budget:       ResourceBudget = field(default_factory=ResourceBudget)
    metrics:      MissionMetrics = field(default_factory=MissionMetrics)

    # Timing
    created_at:   str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at:   Optional[str] = None
    paused_at:    Optional[str] = None
    completed_at: Optional[str] = None
    deadline:     Optional[str] = None

    # Runtime handles (not persisted in snapshots as-is, reconstructed on recovery)
    session_id:   str = ""
    snapshot_path: str = ""

    # Arbitrary metadata
    metadata:     dict[str, Any] = field(default_factory=dict)

    # Final result
    result_summary: str = ""
    report_path:    str = ""

    def elapsed_s(self) -> float:
        if self.started_at is None:
            return 0.0
        start = datetime.fromisoformat(self.started_at)
        end_str = self.completed_at or datetime.now(timezone.utc).isoformat()
        end = datetime.fromisoformat(end_str)
        return (end - start).total_seconds()

    def is_terminal(self) -> bool:
        return self.status in (
            MissionStatus.COMPLETED,
            MissionStatus.FAILED,
            MissionStatus.ARCHIVED,
        )
