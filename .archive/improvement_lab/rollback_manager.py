"""Handles experiment rollback and records lessons learned.

On failure the rollback manager:
  1. Reverts any applied changes inside the sandbox
  2. Destroys the sandbox branch + worktree
  3. Appends a lesson record to lab_history/lessons_learned.json

Lessons are used by the ExperimentGenerator to avoid repeating failed
strategies and to calibrate confidence on future experiments.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from improvement_lab.branch_manager import BranchManager
from improvement_lab.sandbox_runner import SandboxRunner
from improvement_lab.experiment_generator import Experiment

logger = logging.getLogger("improvement_lab.rollback_manager")

_LESSONS_FILE = "lab_history/lessons_learned.json"


@dataclass
class LessonRecord:
    """Immutable record of one experiment outcome for future reference."""
    exp_id: str
    experiment_type: str
    category: str
    hypothesis: str
    outcome: str               # "success" | "failed" | "rolled_back" | "rejected"
    expected_gain_pct: float
    actual_gain_pct: float     # 0.0 if failed/rolled_back
    confidence: float
    failure_reason: str = ""
    target_files: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class RollbackManager:
    """Rolls back failed experiments and maintains the lessons ledger."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self._branch_mgr = BranchManager(project_root)
        self._sandbox_runner = SandboxRunner()

    # ── Public API ────────────────────────────────────────────────────────────

    def rollback(
        self,
        experiment: Experiment,
        sandbox_path: Optional[Path],
        reason: str = "",
    ) -> None:
        """Revert changes and destroy the sandbox.  Never raises."""
        logger.info(f"Rolling back experiment {experiment.id}: {reason or 'no reason given'}")

        if sandbox_path and sandbox_path.exists():
            # Best-effort revert inside sandbox before destroying
            try:
                self._sandbox_runner.revert(sandbox_path, experiment.changes)
            except Exception as exc:
                logger.warning(f"Revert inside sandbox failed (ok, destroying anyway): {exc}")

            try:
                self._branch_mgr.destroy_sandbox(experiment.id)
            except Exception as exc:
                logger.warning(f"Sandbox destroy failed: {exc}")

        self.record_lesson(
            experiment=experiment,
            outcome="rolled_back",
            actual_gain_pct=0.0,
            failure_reason=reason,
        )

    def record_lesson(
        self,
        experiment: Experiment,
        outcome: str,
        actual_gain_pct: float,
        failure_reason: str = "",
    ) -> LessonRecord:
        """Append a LessonRecord to lessons_learned.json."""
        lesson = LessonRecord(
            exp_id=experiment.id,
            experiment_type=experiment.type,
            category=experiment.category,
            hypothesis=experiment.hypothesis,
            outcome=outcome,
            expected_gain_pct=experiment.expected_gain_pct,
            actual_gain_pct=actual_gain_pct,
            confidence=experiment.confidence,
            failure_reason=failure_reason,
            target_files=[c.relative_path for c in experiment.changes],
        )

        lessons_path = self.project_root / _LESSONS_FILE
        lessons_path.parent.mkdir(parents=True, exist_ok=True)

        existing: list[dict] = []
        if lessons_path.exists():
            try:
                existing = json.loads(lessons_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []

        existing.append(asdict(lesson))
        lessons_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        logger.info(f"Lesson recorded: {lesson.exp_id}  outcome={outcome}")
        return lesson

    def load_lessons(self) -> list[LessonRecord]:
        """Return all recorded lessons."""
        lessons_path = self.project_root / _LESSONS_FILE
        if not lessons_path.exists():
            return []
        try:
            data = json.loads(lessons_path.read_text(encoding="utf-8"))
            return [LessonRecord(**item) for item in data]
        except Exception as exc:
            logger.warning(f"Could not load lessons: {exc}")
            return []

    def past_failures_for(self, category: str) -> list[LessonRecord]:
        """Return failed lessons for a given recommendation category."""
        return [
            l for l in self.load_lessons()
            if l.category == category and l.outcome in ("failed", "rolled_back", "rejected")
        ]

    def calibrated_confidence(self, category: str, base_confidence: float) -> float:
        """Reduce confidence if this category has a history of failures."""
        failures = self.past_failures_for(category)
        if not failures:
            return base_confidence
        penalty = min(0.3, len(failures) * 0.07)
        return max(0.1, base_confidence - penalty)
