"""Parallel Optimizer — dynamic worker count adjustment.

Monitors current parallelism vs. historical optimal and adjusts:
  - Too many workers → reduce (conflicts, token waste)
  - Too few workers → increase (underutilization, slow mission)
  - Conflict rate → serialize conflicting tasks

Uses the ParallelExperienceEngine from experience/ to ground decisions
in historical performance data.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class OptimizationResult:
    recommended_workers: int
    current_workers: int
    reason: str
    should_reduce: bool
    should_increase: bool
    confidence: float


class ParallelOptimizer:
    """Recommend optimal worker count based on experience + real-time metrics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._current_workers: int = 1
        self._conflict_count: int = 0
        self._completed_tasks: int = 0

    def set_current_workers(self, count: int) -> None:
        with self._lock:
            self._current_workers = max(1, count)

    def record_conflict(self) -> None:
        with self._lock:
            self._conflict_count += 1

    def record_task_complete(self) -> None:
        with self._lock:
            self._completed_tasks += 1

    def recommend(
        self,
        task_description: str,
        project_root: str = ".",
        max_workers: int = 8,
    ) -> OptimizationResult:
        """Recommend optimal worker count for a task."""
        with self._lock:
            current = self._current_workers
            conflicts = self._conflict_count

        # Query experience engine
        exp_workers = 1
        exp_confidence = 0.30
        try:
            from agent.experience import get_experience
            exp = get_experience(project_root)
            rec = exp.recommend("parallel", task_description)
            if rec and isinstance(rec, dict):
                exp_workers = rec.get("recommended_agent_count", 1)
                exp_confidence = rec.get("confidence", 0.30)
        except Exception:
            pass

        # Budget-aware adjustment
        reduce_for_budget = False
        try:
            from agent.supervisor.budget import budget_controller
            if budget_controller.should_reduce_parallelism():
                reduce_for_budget = True
        except Exception:
            pass

        # Conflict-based reduction: many conflicts → serialize
        conflict_penalty = 0
        if conflicts > 3:
            conflict_penalty = min(3, conflicts // 3)

        # Health-based reduction
        health_ok = True
        try:
            from agent.supervisor.health import health_engine
            h = health_engine.snapshot()
            if h.overall < 60:
                health_ok = False
        except Exception:
            pass

        # Determine recommendation
        if reduce_for_budget or not health_ok:
            recommended = max(1, current - 1)
            reason = "budget pressure" if reduce_for_budget else "low system health"
            should_reduce = recommended < current
            should_increase = False
        elif conflict_penalty > 0:
            recommended = max(1, current - conflict_penalty)
            reason = f"{conflicts} resource conflicts detected"
            should_reduce = True
            should_increase = False
        elif exp_confidence >= 0.60 and exp_workers != current:
            recommended = min(max_workers, max(1, exp_workers))
            should_reduce = recommended < current
            should_increase = recommended > current
            reason = f"experience suggests {exp_workers} workers (confidence {exp_confidence:.0%})"
        else:
            recommended = current
            should_reduce = False
            should_increase = False
            reason = "current parallelism appears optimal"

        return OptimizationResult(
            recommended_workers=recommended,
            current_workers=current,
            reason=reason,
            should_reduce=should_reduce,
            should_increase=should_increase,
            confidence=exp_confidence,
        )

    def reset(self) -> None:
        with self._lock:
            self._conflict_count = 0
            self._completed_tasks = 0


# Module-level singleton
parallel_optimizer = ParallelOptimizer()
