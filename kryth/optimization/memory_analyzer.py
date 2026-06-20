"""Memory analyzer — measures preload, experience, and knowledge graph hit rates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics


@dataclass
class MemoryMissionStats:
    mission_id: str
    speculative_preload_files: int
    worker_pool_preload_files: int
    experience_hits: int
    graph_hits: int
    preload_wait_ms: float
    total_memory_events: int


@dataclass
class MemoryAnalysis:
    per_mission: List[MemoryMissionStats] = field(default_factory=list)
    # Aggregate
    speculative_active_count: int = 0    # missions with preload > 0
    experience_active_count: int = 0     # missions with experience_hits > 0
    worker_pool_active_count: int = 0
    avg_preload_files: float = 0.0
    avg_experience_hits: float = 0.0
    total_preload_files: int = 0
    total_experience_hits: int = 0
    total_graph_hits: int = 0
    # Hit rate estimates (0.0 – 1.0)
    speculative_hit_rate: float = 0.0
    experience_hit_rate: float = 0.0
    # Observations
    memory_subsystem_active: bool = False
    unused_memory_types: List[str] = field(default_factory=list)


def analyze_memory(run: BenchmarkRun) -> MemoryAnalysis:
    n = len(run.missions)
    stats = []
    for m in run.missions:
        mem = m.memory
        stats.append(MemoryMissionStats(
            mission_id=m.mission_id,
            speculative_preload_files=mem.speculative_preload_files,
            worker_pool_preload_files=mem.worker_pool_preload_files,
            experience_hits=mem.experience_hits,
            graph_hits=mem.graph_hits,
            preload_wait_ms=mem.preload_wait_ms,
            total_memory_events=(
                mem.speculative_preload_files
                + mem.worker_pool_preload_files
                + mem.experience_hits
                + mem.graph_hits
            ),
        ))

    analysis = MemoryAnalysis(per_mission=stats)
    if not stats:
        return analysis

    analysis.speculative_active_count = sum(1 for s in stats if s.speculative_preload_files > 0)
    analysis.experience_active_count = sum(1 for s in stats if s.experience_hits > 0)
    analysis.worker_pool_active_count = sum(1 for s in stats if s.worker_pool_preload_files > 0)
    analysis.total_preload_files = sum(s.speculative_preload_files for s in stats)
    analysis.total_experience_hits = sum(s.experience_hits for s in stats)
    analysis.total_graph_hits = sum(s.graph_hits for s in stats)
    analysis.avg_preload_files = analysis.total_preload_files / n
    analysis.avg_experience_hits = analysis.total_experience_hits / n

    # Hit rates: what fraction of missions got each type of memory assistance
    analysis.speculative_hit_rate = analysis.speculative_active_count / n
    analysis.experience_hit_rate = analysis.experience_active_count / n

    analysis.memory_subsystem_active = (
        analysis.total_preload_files > 0
        or analysis.total_experience_hits > 0
        or analysis.total_graph_hits > 0
    )

    if analysis.speculative_active_count == 0:
        analysis.unused_memory_types.append("speculative_preload")
    if analysis.experience_active_count == 0:
        analysis.unused_memory_types.append("experience_engine")
    if analysis.worker_pool_active_count == 0:
        analysis.unused_memory_types.append("worker_pool_preload")
    if analysis.total_graph_hits == 0:
        analysis.unused_memory_types.append("knowledge_graph")

    return analysis
