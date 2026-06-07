"""Risk Engine — per-action risk scoring.

Computes risk for:
  - Execution risk (running a plan)
  - Repair risk (applying an auto-fix)
  - Merge risk (parallel agents touching same files)
  - Deployment risk (pushing/deploying)
  - Data loss risk (deletions, overwrites)

Risk levels: low | medium | high | critical

Published to UIStateManager and consumed by ConfidenceController.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def numeric(self) -> int:
        return {"low": 1, "medium": 2, "high": 3, "critical": 4}[self.value]


@dataclass
class RiskScore:
    level: RiskLevel
    score: float          # 0.0–1.0
    factors: List[str]    # reasons for elevated risk
    mitigations: List[str] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return self.level in (RiskLevel.LOW, RiskLevel.MEDIUM)

    @property
    def label(self) -> str:
        return self.level.value.title()


# Patterns that signal elevated risk in commands/actions
_HIGH_RISK_PATTERNS = [
    (re.compile(r"git push", re.I),        "pushing to remote"),
    (re.compile(r"git reset --hard", re.I), "hard git reset"),
    (re.compile(r"\bdeploy\b", re.I),       "deployment operation"),
    (re.compile(r"rm -rf", re.I),           "recursive delete"),
    (re.compile(r"DROP TABLE", re.I),       "SQL DROP TABLE"),
    (re.compile(r"truncate", re.I),         "truncate operation"),
    (re.compile(r"production|prod\b", re.I), "production environment"),
    (re.compile(r"docker.*stop|kill", re.I), "stopping containers"),
    (re.compile(r"shutdown|halt|reboot", re.I), "system shutdown"),
]

_MEDIUM_RISK_PATTERNS = [
    (re.compile(r"npm install|pip install|cargo install", re.I), "dependency install"),
    (re.compile(r"git merge|git rebase", re.I), "git merge/rebase"),
    (re.compile(r"database|db\b", re.I),   "database operation"),
    (re.compile(r"migration|migrate", re.I), "schema migration"),
    (re.compile(r"chmod|chown", re.I),     "permission change"),
    (re.compile(r"sudo|admin", re.I),      "elevated privileges"),
    (re.compile(r"overwrite|replace.*file", re.I), "file overwrite"),
]


class RiskEngine:
    """Score risk for actions, commands, and execution plans."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def score_command(self, command: str) -> RiskScore:
        """Score the risk of running a shell command."""
        factors: List[str] = []

        for pattern, reason in _HIGH_RISK_PATTERNS:
            if pattern.search(command):
                factors.append(reason)

        if factors:
            return RiskScore(
                level=RiskLevel.HIGH,
                score=0.75 + min(0.25, len(factors) * 0.05),
                factors=factors,
                mitigations=["create git stash before proceeding",
                              "create checkpoint before proceeding"],
            )

        med_factors: List[str] = []
        for pattern, reason in _MEDIUM_RISK_PATTERNS:
            if pattern.search(command):
                med_factors.append(reason)

        if med_factors:
            return RiskScore(
                level=RiskLevel.MEDIUM,
                score=0.40 + min(0.25, len(med_factors) * 0.05),
                factors=med_factors,
            )

        return RiskScore(level=RiskLevel.LOW, score=0.10, factors=[])

    def score_plan(self, steps: List[str]) -> RiskScore:
        """Score the aggregate risk of an execution plan (list of command strings)."""
        if not steps:
            return RiskScore(level=RiskLevel.LOW, score=0.05, factors=[])

        max_score = 0.0
        all_factors: List[str] = []
        for step in steps:
            s = self.score_command(step)
            if s.score > max_score:
                max_score = s.score
            all_factors.extend(s.factors)

        all_factors = list(dict.fromkeys(all_factors))[:5]  # dedup, cap

        if max_score >= 0.75:
            level = RiskLevel.HIGH
        elif max_score >= 0.40:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW

        return RiskScore(level=level, score=max_score, factors=all_factors)

    def score_merge(self, file_a: str, file_b: str) -> RiskScore:
        """Score the risk of two agents touching the same file."""
        if not file_a or not file_b or file_a != file_b:
            return RiskScore(level=RiskLevel.LOW, score=0.1, factors=[])
        return RiskScore(
            level=RiskLevel.HIGH,
            score=0.80,
            factors=[f"Two agents targeting same file: {file_a}"],
            mitigations=["serialize access", "use ownership lock"],
        )

    def score_deployment(self, target: str = "") -> RiskScore:
        """Score deployment risk based on target environment."""
        if any(w in target.lower() for w in ("prod", "production", "live")):
            return RiskScore(
                level=RiskLevel.CRITICAL,
                score=0.95,
                factors=["Production deployment"],
                mitigations=["manual confirmation required"],
            )
        if any(w in target.lower() for w in ("staging", "stage", "qa")):
            return RiskScore(
                level=RiskLevel.MEDIUM,
                score=0.50,
                factors=["Staging deployment"],
            )
        return RiskScore(level=RiskLevel.LOW, score=0.20, factors=["Development deployment"])

    def score_data_loss(self, operation: str) -> RiskScore:
        """Score risk of data loss from an operation."""
        op = operation.lower()
        if any(w in op for w in ("delete", "drop", "truncate", "rm -rf", "wipe")):
            return RiskScore(
                level=RiskLevel.CRITICAL,
                score=0.90,
                factors=["Destructive data operation"],
                mitigations=["backup first", "create checkpoint"],
            )
        if any(w in op for w in ("overwrite", "replace", "reset")):
            return RiskScore(
                level=RiskLevel.HIGH,
                score=0.70,
                factors=["Potential data overwrite"],
                mitigations=["snapshot before proceeding"],
            )
        return RiskScore(level=RiskLevel.LOW, score=0.05, factors=[])

    def aggregate(self, scores: List[RiskScore]) -> RiskScore:
        """Return the highest-risk score from a list."""
        if not scores:
            return RiskScore(level=RiskLevel.LOW, score=0.0, factors=[])
        return max(scores, key=lambda s: s.score)


# Module-level singleton
risk_engine = RiskEngine()
