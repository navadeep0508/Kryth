"""Metric data structures for the autonomous evaluation framework."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


# ── Per-dimension review result ───────────────────────────────────────────────

@dataclass
class ReviewScore:
    dimension: str
    score: int = 0           # 0-100
    weight: float = 1.0      # contribution to overall (relative)
    findings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    reviewer: str = "static"  # "static" | "llm" | "hybrid"
    duration_s: float = 0.0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── All 12 dimension scores ───────────────────────────────────────────────────

@dataclass
class EvaluationScores:
    # Primary dimensions (weighted in overall)
    correctness: int = 0         # weight 0.20
    code_quality: int = 0        # weight 0.15
    architecture: int = 0        # weight 0.15
    testing: int = 0             # weight 0.15
    performance: int = 0         # weight 0.10
    security: int = 0            # weight 0.10
    maintainability: int = 0     # weight 0.10
    documentation: int = 0       # weight 0.05
    # Supplementary dimensions (tracked but not in overall formula)
    parallel_efficiency: int = 0
    memory_reuse: int = 0
    recovery_quality: int = 0
    build_success: int = 0
    test_success: int = 0
    # Computed
    overall: int = 0

    # Weights for computing overall
    _WEIGHTS: dict[str, float] = field(default_factory=lambda: {
        "correctness":    0.20,
        "code_quality":   0.15,
        "architecture":   0.15,
        "testing":        0.15,
        "performance":    0.10,
        "security":       0.10,
        "maintainability": 0.10,
        "documentation":  0.05,
    }, repr=False)

    def compute_overall(self) -> int:
        total = 0.0
        for dim, w in self._WEIGHTS.items():
            total += getattr(self, dim, 0) * w
        self.overall = int(round(total))
        return self.overall

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d.pop("_WEIGHTS", None)
        return d

    def grade(self) -> str:
        s = self.overall
        if s >= 90: return "A"
        if s >= 80: return "B"
        if s >= 70: return "C"
        if s >= 60: return "D"
        return "F"


# ── Quality rule hits ─────────────────────────────────────────────────────────

@dataclass
class RuleViolation:
    rule_id: str
    severity: str        # "error" | "warning" | "info"
    file: str
    line: int
    message: str
    penalty: int = 0     # points subtracted from code_quality

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class RuleReward:
    rule_id: str
    file: str
    message: str
    bonus: int = 0       # points added to code_quality

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Full evaluation result for one mission ────────────────────────────────────

@dataclass
class EvaluationResult:
    mission_id: str = ""
    mission_name: str = ""
    workspace: str = ""
    timestamp: str = ""
    run_id: str = ""          # links back to benchmark run
    scores: EvaluationScores = field(default_factory=EvaluationScores)
    review_scores: dict[str, ReviewScore] = field(default_factory=dict)
    violations: list[RuleViolation] = field(default_factory=list)
    rewards: list[RuleReward] = field(default_factory=list)
    files_evaluated: list[str] = field(default_factory=list)
    evaluation_duration_s: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "mission_id": self.mission_id,
            "mission_name": self.mission_name,
            "workspace": self.workspace,
            "timestamp": self.timestamp,
            "run_id": self.run_id,
            "scores": self.scores.to_dict(),
            "review_scores": {k: v.to_dict() for k, v in self.review_scores.items()},
            "violations": [v.to_dict() for v in self.violations],
            "rewards": [r.to_dict() for r in self.rewards],
            "files_evaluated": self.files_evaluated,
            "evaluation_duration_s": self.evaluation_duration_s,
            "error": self.error,
        }
        return d


# ── Collection of evaluations for a full benchmark run ───────────────────────

@dataclass
class EvaluationRun:
    run_id: str = ""
    benchmark_run_id: str = ""
    timestamp: str = ""
    kryth_version: str = "unknown"
    results: list[EvaluationResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def avg_overall(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.scores.overall for r in self.results) / len(self.results)

    @property
    def avg_by_dimension(self) -> dict[str, float]:
        dims = [
            "correctness", "code_quality", "architecture", "testing",
            "performance", "security", "maintainability", "documentation",
            "parallel_efficiency", "memory_reuse",
        ]
        n = len(self.results) or 1
        return {
            d: sum(getattr(r.scores, d, 0) for r in self.results) / n
            for d in dims
        }

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "benchmark_run_id": self.benchmark_run_id,
            "timestamp": self.timestamp,
            "kryth_version": self.kryth_version,
            "results": [r.to_dict() for r in self.results],
        }
