"""Executor Selector — scores every registered executor and picks the best one.

Scoring criteria (from the UAL spec):
  - Speed (lower estimated_time_ms = higher score)
  - Confidence (executor's self-reported estimate_confidence)
  - Historical success rate (from ActionMemory)
  - Risk alignment (high-risk actions prefer proven executors)
  - User preferences (preferred_executors hint)
  - Current availability (parallel load — future)

The selector never hard-codes tool choices. The LLM populates
`action.preferred_executors` as a hint; the selector makes the final call.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from agent.action.action import Action, ActionRisk
from agent.action.action_registry import ActionRegistry, BaseExecutor, get_registry

logger = logging.getLogger(__name__)


@dataclass
class ExecutorScore:
    executor: BaseExecutor
    score: float                   # 0–100
    confidence: float
    speed_ms: float
    history_rate: float            # 0–1 from ActionMemory; -1 = no history
    selected: bool = False


class ExecutorSelector:
    """Scores all candidate executors and returns the best one."""

    def __init__(
        self,
        registry: ActionRegistry | None = None,
        memory=None,   # Optional[ActionMemory] — avoids circular import
    ) -> None:
        self._registry = registry or get_registry()
        self._memory = memory

    # ── Public API ────────────────────────────────────────────────────

    def select(self, action: Action) -> BaseExecutor | None:
        """Return the best executor for the action, or None if none can handle it."""
        scores = self.score_all(action)
        if not scores:
            logger.warning("No executor can handle action '%s': %s", action.id, action.goal)
            return None
        best = scores[0]
        best.selected = True
        logger.info(
            "Selected executor '%s' for '%s' (score=%.1f conf=%.2f hist=%.2f)",
            best.executor.name, action.goal[:60],
            best.score, best.confidence, best.history_rate,
        )
        return best.executor

    def score_all(self, action: Action) -> list[ExecutorScore]:
        """Return all capable executors sorted by score descending."""
        scores = []
        for ex in self._registry.all():
            if not ex.can_handle(action):
                continue
            score_obj = self._score(ex, action)
            scores.append(score_obj)
        scores.sort(key=lambda s: s.score, reverse=True)
        return scores

    # ── Scoring ───────────────────────────────────────────────────────

    def _score(self, ex: BaseExecutor, action: Action) -> ExecutorScore:
        confidence    = ex.estimate_confidence(action)
        speed_ms      = ex.estimate_speed_ms(action)
        history_rate  = self._history_rate(ex.name, action.goal)

        # Confidence component (0–40 pts)
        conf_score = confidence * 40.0

        # Speed component (0–20 pts): faster = higher score
        # normalise: 0 ms → 20 pts, 60s → 0 pts
        speed_score = max(0.0, 20.0 * (1.0 - min(speed_ms, 60_000) / 60_000))

        # History component (0–25 pts)
        if history_rate >= 0:
            hist_score = history_rate * 25.0
        else:
            hist_score = 12.5   # no history → neutral

        # Preference hint (0–10 pts)
        pref_score = 10.0 if ex.name in action.preferred_executors else 0.0

        # Risk penalty: for CRITICAL/HIGH actions, reduce low-confidence executors
        risk_penalty = 0.0
        if action.risk in (ActionRisk.HIGH, ActionRisk.CRITICAL) and confidence < 0.6:
            risk_penalty = 5.0

        total = conf_score + speed_score + hist_score + pref_score - risk_penalty

        return ExecutorScore(
            executor=ex,
            score=round(total, 2),
            confidence=confidence,
            speed_ms=speed_ms,
            history_rate=history_rate,
        )

    def _history_rate(self, executor_name: str, goal: str) -> float:
        """Return success rate from ActionMemory, or -1 if unavailable."""
        if self._memory is None:
            return -1.0
        try:
            return self._memory.success_rate(executor_name, goal)
        except Exception:
            return -1.0
