"""Strategic decision engine (Phase K).

Generates organizational recommendations — scale workers up/down, increase/reduce
parallelism, split/merge/escalate/delay a mission — from observed signals. It is
strictly **advisory**: every recommendation carries ``auto_execute=False``. A
human (or the Executive layer) decides whether to act; this engine never changes
state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Recommendation:
    action: str            # hire_workers | reduce_workers | increase_parallelism | …
    target: str            # mission id / department / "org"
    reason: str
    confidence: float      # 0–1
    auto_execute: bool = False   # ALWAYS False — recommendations only

    def to_dict(self) -> dict:
        return {"action": self.action, "target": self.target, "reason": self.reason,
                "confidence": round(self.confidence, 2), "auto_execute": False}


@dataclass
class StrategySignal:
    target: str = "org"
    queue_depth: int = 0
    idle_workers: int = 0
    workers: int = 0
    risk: str = "low"               # low | medium | high
    progress: float = 0.0           # 0–100
    independent_units: int = 1      # decomposable streams remaining
    blocked_ratio: float = 0.0      # fraction of tasks blocked
    deadline_pressure: bool = False


def recommend(signal: StrategySignal) -> List[Recommendation]:
    """Produce ranked recommendations for a signal. Recommendations only."""
    recs: List[Recommendation] = []

    if signal.queue_depth > max(4, signal.workers) and signal.idle_workers == 0:
        recs.append(Recommendation(
            "hire_workers", signal.target,
            f"queue depth {signal.queue_depth} with no idle workers", 0.8))
    if signal.idle_workers >= 2 and signal.queue_depth == 0:
        recs.append(Recommendation(
            "reduce_workers", signal.target,
            f"{signal.idle_workers} idle workers and empty queue", 0.75))

    if signal.independent_units >= 4 and signal.workers < signal.independent_units:
        recs.append(Recommendation(
            "increase_parallelism", signal.target,
            f"{signal.independent_units} independent streams but only {signal.workers} workers", 0.7))
    if signal.independent_units <= 1 and signal.workers > 2:
        recs.append(Recommendation(
            "reduce_parallelism", signal.target,
            "work is largely sequential — fewer workers reduce coordination cost", 0.65))

    if signal.independent_units >= 8:
        recs.append(Recommendation(
            "split_mission", signal.target,
            f"{signal.independent_units} streams — split into sub-missions for clarity", 0.6))

    if signal.risk == "high" or signal.blocked_ratio >= 0.5:
        recs.append(Recommendation(
            "escalate_mission", signal.target,
            f"risk={signal.risk}, blocked_ratio={signal.blocked_ratio:.0%}", 0.85))

    if signal.deadline_pressure and signal.progress < 50:
        recs.append(Recommendation(
            "delay_mission", signal.target,
            "behind schedule under deadline pressure — consider rescoping or delaying", 0.55))

    recs.sort(key=lambda r: r.confidence, reverse=True)
    return recs
