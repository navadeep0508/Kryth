"""Executor Controller — observes per-executor performance and switches if needed."""

from __future__ import annotations

import logging
from typing import Optional
from agent.executive.executive_state import ExecutiveDecision, ExecutiveState

logger = logging.getLogger(__name__)

_MIN_EXECUTOR_SUCCESS_RATE = 0.50   # switch executor if success rate drops below this
_MIN_ATTEMPTS_BEFORE_SWITCH = 3     # need at least this many attempts before deciding


class ExecutorController:
    """Monitors executor success rates and recommends switching."""

    def assess(
        self,
        state: ExecutiveState,
        executor_stats: dict[str, dict] | None = None,
    ) -> ExecutiveDecision:
        if not executor_stats:
            return ExecutiveDecision.CONTINUE

        poor: list[str] = []
        for name, stats in executor_stats.items():
            total = stats.get("total", 0)
            rate  = stats.get("rate", 1.0)
            if total >= _MIN_ATTEMPTS_BEFORE_SWITCH and rate < _MIN_EXECUTOR_SUCCESS_RATE:
                poor.append(f"{name}:{rate:.0%}")

        if poor:
            logger.warning(
                "ExecutorController: poor executors %s — recommending switch", poor
            )
            state.annotations["poor_executors"] = poor
            return ExecutiveDecision.REPLAN  # signal: replanner should reassign executors

        return ExecutiveDecision.CONTINUE

    def best_executor(
        self,
        executor_stats: dict[str, dict],
        candidates: list[str],
    ) -> Optional[str]:
        """Return the highest-success-rate executor from candidates."""
        if not candidates:
            return None
        scored = sorted(
            [(name, executor_stats.get(name, {}).get("rate", 0.5)) for name in candidates],
            key=lambda x: x[1],
            reverse=True,
        )
        return scored[0][0]
