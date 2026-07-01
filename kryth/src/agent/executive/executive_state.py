"""Executive state — dataclasses shared across the Executive Intelligence Layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ExecutiveDecision(str, Enum):
    CONTINUE   = "continue"
    PAUSE      = "pause"
    REPLAN     = "replan"
    ABORT      = "abort"
    ESCALATE   = "escalate"
    SPAWN      = "spawn"      # spawn more workers
    RETIRE     = "retire"     # retire idle workers


class RiskLevel(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


@dataclass
class ConfidenceScore:
    mission:     float = 1.0   # 0–1
    planning:    float = 1.0
    execution:   float = 1.0
    verification: float = 1.0
    recovery:    float = 1.0
    memory:      float = 1.0
    executors:   float = 1.0

    @property
    def overall(self) -> float:
        weights = [0.25, 0.10, 0.30, 0.15, 0.10, 0.05, 0.05]
        vals    = [self.mission, self.planning, self.execution,
                   self.verification, self.recovery, self.memory, self.executors]
        return sum(w * v for w, v in zip(weights, vals))


@dataclass
class BudgetState:
    token_limit:        int   = 500_000
    tokens_used:        int   = 0
    llm_calls:          int   = 0
    api_retries:        int   = 0
    tool_calls:         int   = 0
    elapsed_s:          float = 0.0
    estimated_total_s:  float = 0.0

    @property
    def token_pct(self) -> float:
        return self.tokens_used / self.token_limit * 100 if self.token_limit else 0.0

    @property
    def over_budget(self) -> bool:
        return self.tokens_used >= self.token_limit


@dataclass
class QualityState:
    test_pass_rate:    float = 1.0   # 0–1
    verification_rate: float = 1.0
    security_score:    float = 1.0
    eval_score:        float = 1.0

    @property
    def overall(self) -> float:
        return (self.test_pass_rate + self.verification_rate +
                self.security_score + self.eval_score) / 4


@dataclass
class TeamState:
    active_workers: int  = 0
    idle_workers:   int  = 0
    busy_workers:   int  = 0
    parallel_peak:  int  = 0      # deprecated: sequential-only execution
    parallel_efficiency_pct: float = 0.0  # deprecated: always 0.0


@dataclass
class MissionHealth:
    total_actions:     int   = 0
    succeeded:         int   = 0
    failed:            int   = 0
    running:           int   = 0
    pending:           int   = 0
    skipped:           int   = 0
    replan_count:      int   = 0
    elapsed_s:         float = 0.0
    estimated_total_s: float = 0.0

    @property
    def progress_pct(self) -> float:
        done = self.succeeded + self.skipped
        return done / self.total_actions * 100 if self.total_actions else 0.0

    @property
    def failure_rate(self) -> float:
        total = self.succeeded + self.failed
        return self.failed / total if total else 0.0


@dataclass
class ExecutiveState:
    """Full executive view — assembled by the Executive from all subsystems."""
    session_id: str = ""
    mission:    str = ""

    # Sub-state
    health:     MissionHealth  = field(default_factory=MissionHealth)
    budget:     BudgetState    = field(default_factory=BudgetState)
    quality:    QualityState   = field(default_factory=QualityState)
    confidence: ConfidenceScore = field(default_factory=ConfidenceScore)
    team:       TeamState      = field(default_factory=TeamState)
    risk:       RiskLevel      = RiskLevel.LOW

    # History
    decisions:   list[tuple[str, ExecutiveDecision]] = field(default_factory=list)
    escalations: list[str]                           = field(default_factory=list)

    # Current recommendation
    recommendation: ExecutiveDecision = ExecutiveDecision.CONTINUE
    rationale:      str               = ""

    # Arbitrary annotations from controllers
    annotations: dict[str, Any] = field(default_factory=dict)
