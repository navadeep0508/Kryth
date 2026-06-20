"""Scheduler analyzer — measures parallel dispatch and batch scheduling efficiency."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics


@dataclass
class SchedulerMissionStats:
    mission_id: str
    total_batches: int          # turns with at least 1 tool
    parallel_batches: int       # turns with >1 tool
    serial_batches: int         # turns with exactly 1 tool
    max_batch_size: int
    total_parallel_calls: int
    parallel_efficiency_pct: float
    parallelism_ratio: float    # parallel_batches / total_batches


@dataclass
class SchedulerAnalysis:
    per_mission: List[SchedulerMissionStats] = field(default_factory=list)
    avg_parallel_efficiency_pct: float = 0.0
    avg_parallelism_ratio: float = 0.0
    avg_max_batch_size: float = 0.0
    total_parallel_calls: int = 0
    best_mission: str = ""      # highest parallel efficiency
    worst_mission: str = ""     # lowest parallel efficiency
    # Observations
    batch_scheduling_healthy: bool = False   # avg > 60%
    serial_dominant: bool = False            # avg ratio < 0.3 → most turns are serial


def analyze_scheduler(run: BenchmarkRun) -> SchedulerAnalysis:
    stats = []
    for m in run.missions:
        p = m.parallel
        serial = p.total_tool_batches - p.parallel_batches
        ratio = p.parallel_batches / max(p.total_tool_batches, 1)

        stats.append(SchedulerMissionStats(
            mission_id=m.mission_id,
            total_batches=p.total_tool_batches,
            parallel_batches=p.parallel_batches,
            serial_batches=serial,
            max_batch_size=p.max_batch_size,
            total_parallel_calls=p.total_parallel_calls,
            parallel_efficiency_pct=p.parallel_efficiency_pct,
            parallelism_ratio=ratio,
        ))

    analysis = SchedulerAnalysis(per_mission=stats)
    if not stats:
        return analysis

    n = len(stats)
    analysis.avg_parallel_efficiency_pct = (
        sum(s.parallel_efficiency_pct for s in stats) / n
    )
    analysis.avg_parallelism_ratio = sum(s.parallelism_ratio for s in stats) / n
    analysis.avg_max_batch_size = sum(s.max_batch_size for s in stats) / n
    analysis.total_parallel_calls = sum(s.total_parallel_calls for s in stats)

    if stats:
        best = max(stats, key=lambda s: s.parallel_efficiency_pct)
        worst = min(stats, key=lambda s: s.parallel_efficiency_pct)
        analysis.best_mission = best.mission_id
        analysis.worst_mission = worst.mission_id

    analysis.batch_scheduling_healthy = analysis.avg_parallel_efficiency_pct >= 60.0
    analysis.serial_dominant = analysis.avg_parallelism_ratio < 0.3

    return analysis
