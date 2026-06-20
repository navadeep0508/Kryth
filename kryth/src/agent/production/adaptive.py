"""Adaptive parallelism controller (Phase 8).

The Mission Estimator picks a *static* worker count up front. Real workload is
bursty: a queue can pile up (under-provisioned) or workers can sit idle
(over-provisioned). This controller watches runtime signals and *recommends* a
new worker count — scale up when the queue grows, down when the idle ratio
rises — converging on the count that keeps workers busy without over-spawning.

It is a pure advisor: `recommend(signal)` returns a target; the orchestrator
decides whether to act. It never spawns/kills workers itself, so it cannot
destabilize the scheduler. Bounded and hysteretic (won't thrash).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParallelismSignal:
    workers: int            # current worker count
    running: int            # workers doing work
    queue_depth: int        # runnable tasks waiting for a worker
    idle: int = 0           # workers with nothing to do
    max_workers: int = 16   # hard cap

    @property
    def idle_ratio(self) -> float:
        return self.idle / self.workers if self.workers else 0.0


@dataclass
class ParallelismDecision:
    target_workers: int
    action: str             # "scale_up" | "scale_down" | "hold"
    reason: str
    peak_efficiency: float  # running/workers at the recommended target (0–1)


# Thresholds (hysteresis band so we don't oscillate).
_QUEUE_PRESSURE = 2          # queued tasks per spare worker before scaling up
_IDLE_SLACK = 0.34           # >1/3 idle → scale down
_MIN_WORKERS = 1


def recommend(signal: ParallelismSignal) -> ParallelismDecision:
    """Recommend a worker count from the current signal.

    Scale UP when there's queued work and few/no idle workers (real backlog).
    Scale DOWN when a meaningful fraction of workers are idle. Otherwise hold.
    """
    w = max(_MIN_WORKERS, signal.workers)
    cap = max(_MIN_WORKERS, signal.max_workers)

    backlog = signal.queue_depth
    spare = signal.idle

    # Scale up: backlog and not enough spare capacity to absorb it.
    if backlog > 0 and spare <= 1 and w < cap:
        # add roughly one worker per _QUEUE_PRESSURE queued tasks, bounded.
        add = max(1, backlog // _QUEUE_PRESSURE)
        target = min(cap, w + add)
        if target > w:
            return ParallelismDecision(
                target, "scale_up",
                f"queue depth {backlog} with {spare} spare — add {target - w} worker(s)",
                _efficiency(signal.running + min(add, backlog), target))

    # Scale down: too many idle workers and no backlog.
    if signal.idle_ratio > _IDLE_SLACK and backlog == 0 and w > _MIN_WORKERS:
        # drop half the idle workers (keep one in reserve for burst).
        drop = max(1, signal.idle // 2)
        target = max(_MIN_WORKERS, w - drop)
        if target < w:
            return ParallelismDecision(
                target, "scale_down",
                f"{signal.idle}/{w} workers idle, no backlog — release {w - target}",
                _efficiency(signal.running, target))

    return ParallelismDecision(w, "hold", "utilization within target band",
                               _efficiency(signal.running, w))


def _efficiency(running: int, workers: int) -> float:
    return round(min(1.0, running / workers), 3) if workers else 0.0


class AdaptiveController:
    """Stateful wrapper: remembers the last target and the peak parallelism
    observed (Phase 8 "Optimal Worker Count" / "Peak Efficiency")."""

    def __init__(self, *, max_workers: int = 16):
        self.max_workers = max_workers
        self.peak_parallelism = 0
        self.optimal_workers = 1
        self._best_eff = 0.0

    def observe(self, signal: ParallelismSignal) -> ParallelismDecision:
        signal.max_workers = self.max_workers
        self.peak_parallelism = max(self.peak_parallelism, signal.running)
        eff = _efficiency(signal.running, signal.workers)
        if eff >= self._best_eff:
            self._best_eff = eff
            self.optimal_workers = signal.workers
        return recommend(signal)

    def report(self) -> dict:
        return {
            "peak_parallelism": self.peak_parallelism,
            "optimal_workers": self.optimal_workers,
            "peak_efficiency": round(self._best_eff, 3),
        }
