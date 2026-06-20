"""Worker analyzer — measures agent utilization and scheduling efficiency."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics


@dataclass
class WorkerMissionStats:
    mission_id: str
    peak_active: int
    total_spawned: int
    dynamic_spawns: int
    idle_slots_at_end: int
    work_steal_count: int
    utilization_pct: float
    verdict: str   # OPTIMAL / UNDER_PROVISIONED / OVER_PROVISIONED / SINGLE_AGENT


@dataclass
class WorkerAnalysis:
    per_mission: List[WorkerMissionStats] = field(default_factory=list)
    avg_peak_active: float = 0.0
    avg_utilization_pct: float = 0.0
    total_work_steals: int = 0
    missions_single_agent: int = 0
    missions_multi_agent: int = 0
    missions_idle_waste: int = 0    # ended with idle slots
    under_provisioned: List[str] = field(default_factory=list)  # mission IDs
    over_provisioned: List[str] = field(default_factory=list)


def _classify(m: MissionMetrics) -> WorkerMissionStats:
    ag = m.agents
    if ag.peak_active <= 1:
        verdict = "SINGLE_AGENT"
    elif ag.utilization_pct > 75:
        verdict = "OPTIMAL"
    elif ag.utilization_pct < 40 and ag.idle_slots_at_end > 0:
        verdict = "OVER_PROVISIONED"
    else:
        # Multi-file missions with single agent signal under-provisioning
        verdict = "UNDER_PROVISIONED" if m.files_written > 8 and ag.peak_active <= 1 else "OPTIMAL"

    return WorkerMissionStats(
        mission_id=m.mission_id,
        peak_active=ag.peak_active,
        total_spawned=ag.total_spawned,
        dynamic_spawns=ag.dynamic_spawns,
        idle_slots_at_end=ag.idle_slots_at_end,
        work_steal_count=ag.work_steal_count,
        utilization_pct=ag.utilization_pct,
        verdict=verdict,
    )


def analyze_workers(run: BenchmarkRun) -> WorkerAnalysis:
    stats = [_classify(m) for m in run.missions]
    analysis = WorkerAnalysis(per_mission=stats)

    if stats:
        analysis.avg_peak_active = sum(s.peak_active for s in stats) / len(stats)
        analysis.avg_utilization_pct = sum(s.utilization_pct for s in stats) / len(stats)
    analysis.total_work_steals = sum(s.work_steal_count for s in stats)
    analysis.missions_single_agent = sum(1 for s in stats if s.peak_active <= 1)
    analysis.missions_multi_agent = sum(1 for s in stats if s.peak_active > 1)
    analysis.missions_idle_waste = sum(1 for s in stats if s.idle_slots_at_end > 0)
    analysis.under_provisioned = [s.mission_id for s in stats if s.verdict == "UNDER_PROVISIONED"]
    analysis.over_provisioned = [s.mission_id for s in stats if s.verdict == "OVER_PROVISIONED"]
    return analysis
