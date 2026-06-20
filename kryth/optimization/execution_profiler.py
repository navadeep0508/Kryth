"""Execution profiler — breaks each mission into timing phases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics


@dataclass
class MissionProfile:
    mission_id: str
    mission_name: str
    success: bool
    duration_ms: float

    # Phase durations (ms)
    planning_ms: float = 0.0        # t=0 → first tool call
    exploration_ms: float = 0.0     # first read → first write
    implementation_ms: float = 0.0  # first write → end
    testing_ms: float = 0.0         # first test → end (0 if no tests)

    # Phase percentages
    planning_pct: float = 0.0
    exploration_pct: float = 0.0
    implementation_pct: float = 0.0
    testing_pct: float = 0.0

    # Efficiency ratios
    read_write_ratio: float = 0.0   # files_read / files_written
    tools_per_turn: float = 0.0     # total_tool_calls / turns_used

    # Flags
    has_tests: bool = False
    is_streaming: bool = False
    is_multi_agent: bool = False


@dataclass
class ExecutionProfile:
    missions: List[MissionProfile] = field(default_factory=list)
    avg_planning_ms: float = 0.0
    avg_exploration_ms: float = 0.0
    avg_implementation_ms: float = 0.0
    avg_planning_pct: float = 0.0
    avg_exploration_pct: float = 0.0
    avg_implementation_pct: float = 0.0
    avg_read_write_ratio: float = 0.0
    avg_tools_per_turn: float = 0.0
    missions_with_tests: int = 0
    missions_streaming: int = 0
    missions_multi_agent: int = 0


def _profile_mission(m: MissionMetrics) -> MissionProfile:
    t = m.timings
    dur = max(t.duration_ms, 1.0)

    planning_ms = max(t.first_tool_call_ms, 0.0) if t.first_tool_call_ms >= 0 else 0.0

    if t.first_read_ms >= 0 and t.first_write_ms >= 0:
        exploration_ms = max(t.first_write_ms - t.first_read_ms, 0.0)
    elif t.first_write_ms >= 0 and t.first_tool_call_ms >= 0:
        exploration_ms = max(t.first_write_ms - t.first_tool_call_ms, 0.0)
    else:
        exploration_ms = 0.0

    implementation_ms = max(dur - (t.first_write_ms if t.first_write_ms >= 0 else dur), 0.0)
    testing_ms = max(dur - t.first_test_ms, 0.0) if t.first_test_ms >= 0 else 0.0

    read_write_ratio = m.files_read / max(m.files_written, 1)
    tools_per_turn = m.total_tool_calls / max(m.turns_used, 1)

    p = MissionProfile(
        mission_id=m.mission_id,
        mission_name=m.mission_name,
        success=m.success,
        duration_ms=dur,
        planning_ms=planning_ms,
        exploration_ms=exploration_ms,
        implementation_ms=implementation_ms,
        testing_ms=testing_ms,
        planning_pct=planning_ms / dur * 100,
        exploration_pct=exploration_ms / dur * 100,
        implementation_pct=implementation_ms / dur * 100,
        testing_pct=testing_ms / dur * 100,
        read_write_ratio=read_write_ratio,
        tools_per_turn=tools_per_turn,
        has_tests=t.first_test_ms >= 0,
        is_streaming=m.streaming.streams_started > 0,
        is_multi_agent=m.agents.peak_active > 1,
    )
    return p


def profile_run(run: BenchmarkRun) -> ExecutionProfile:
    profiles = [_profile_mission(m) for m in run.missions]
    passed = [p for p in profiles if p.success]
    n = max(len(passed), 1)

    ep = ExecutionProfile(missions=profiles)
    if passed:
        ep.avg_planning_ms = sum(p.planning_ms for p in passed) / n
        ep.avg_exploration_ms = sum(p.exploration_ms for p in passed) / n
        ep.avg_implementation_ms = sum(p.implementation_ms for p in passed) / n
        ep.avg_planning_pct = sum(p.planning_pct for p in passed) / n
        ep.avg_exploration_pct = sum(p.exploration_pct for p in passed) / n
        ep.avg_implementation_pct = sum(p.implementation_pct for p in passed) / n
        ep.avg_read_write_ratio = sum(p.read_write_ratio for p in passed) / n
        ep.avg_tools_per_turn = sum(p.tools_per_turn for p in passed) / n
    ep.missions_with_tests = sum(1 for p in profiles if p.has_tests)
    ep.missions_streaming = sum(1 for p in profiles if p.is_streaming)
    ep.missions_multi_agent = sum(1 for p in profiles if p.is_multi_agent)
    return ep
