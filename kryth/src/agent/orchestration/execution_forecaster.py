"""ExecutionForecaster — V5 Phase 9.

Predicts, and continuously updates as milestones complete:
  * Remaining time
  * Remaining tokens
  * Remaining cost
  * Remaining deliverables
  * Critical path completion ETA
  * Milestone ETA
  * Mission ETA

Pure velocity-based extrapolation from completed-so-far samples — no ML,
just rate = completed / elapsed, projected onto what's left. Confidence
rises with sample count (more milestones observed = better rate estimate).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class ForecastSnapshot:
    milestones_done: int
    milestones_total: int
    deliverables_done: int
    deliverables_total: int
    elapsed_s: float
    tokens_used: int
    estimated_remaining_s: float
    estimated_remaining_tokens: int
    estimated_remaining_cost: float
    estimated_remaining_deliverables: int
    mission_eta_s: float                  # elapsed + remaining
    critical_path_eta_s: float
    confidence: str = "low"                # low / medium / high — sample-size based

    def summary(self) -> str:
        return (
            f"Forecast: {self.milestones_done}/{self.milestones_total} milestones done  ·  "
            f"ETA {self.estimated_remaining_s:.0f}s remaining  ·  "
            f"mission total ≈{self.mission_eta_s:.0f}s  ·  "
            f"~{self.estimated_remaining_tokens:,} tokens / ${self.estimated_remaining_cost:.2f} left  ·  "
            f"confidence={self.confidence}"
        )


class ExecutionForecaster:
    """Call update() after each milestone completes to get a fresh forecast."""

    def __init__(
        self,
        milestones_total: int,
        deliverables_total: int,
        token_budget: int = 0,
        cost_per_1k_tokens: float = 1.0,
    ) -> None:
        self._milestones_total = max(1, milestones_total)
        self._deliverables_total = max(0, deliverables_total)
        self._token_budget = token_budget
        self._cost_per_1k = cost_per_1k_tokens
        self._start = time.monotonic()
        self._samples: List[Tuple[float, int, int, int]] = []   # (elapsed, ms_done, d_done, tokens)
        self._critical_path_total_s: float = 0.0
        self._critical_path_done_s: float = 0.0
        self._last_snapshot: Optional[ForecastSnapshot] = None

    def update(
        self,
        milestones_done: int,
        deliverables_done: int,
        tokens_used: int = 0,
        critical_path_elapsed_s: float = 0.0,
        critical_path_total_estimate_s: float = 0.0,
    ) -> ForecastSnapshot:
        elapsed = time.monotonic() - self._start
        self._samples.append((elapsed, milestones_done, deliverables_done, tokens_used))
        if critical_path_total_estimate_s:
            self._critical_path_total_s = critical_path_total_estimate_s
        self._critical_path_done_s = critical_path_elapsed_s

        n = len(self._samples)
        confidence = "low" if n < 2 else ("medium" if n < 4 else "high")

        ms_rate = milestones_done / elapsed if elapsed > 0 else 0.0
        ms_remaining = max(0, self._milestones_total - milestones_done)
        remaining_s = ms_remaining / ms_rate if ms_rate > 0 else 0.0

        d_remaining = max(0, self._deliverables_total - deliverables_done)

        token_rate = tokens_used / elapsed if elapsed > 0 else 0.0
        remaining_tokens = int(token_rate * remaining_s) if remaining_s and token_rate else 0
        remaining_cost = remaining_tokens / 1000 * self._cost_per_1k

        cp_remaining = max(0.0, self._critical_path_total_s - self._critical_path_done_s)

        snapshot = ForecastSnapshot(
            milestones_done=milestones_done,
            milestones_total=self._milestones_total,
            deliverables_done=deliverables_done,
            deliverables_total=self._deliverables_total,
            elapsed_s=elapsed,
            tokens_used=tokens_used,
            estimated_remaining_s=remaining_s,
            estimated_remaining_tokens=remaining_tokens,
            estimated_remaining_cost=remaining_cost,
            estimated_remaining_deliverables=d_remaining,
            mission_eta_s=elapsed + remaining_s,
            critical_path_eta_s=cp_remaining,
            confidence=confidence,
        )
        self._last_snapshot = snapshot
        return snapshot

    @property
    def latest(self) -> Optional[ForecastSnapshot]:
        return self._last_snapshot
