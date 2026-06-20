"""OverheadProfiler — V6 Phase 6.

Measures time spent in each V5/V6 orchestration subsystem so we know exactly
where the overhead is and can minimize it.

Subsystems tracked:
  planner          — project_planner.plan_project() LLM call
  dag_build        — dag_from_plan() / build_task_dag()
  milestone_build  — plan.ensure_structured_milestones()
  team_build       — team_from_plan()
  approval_gate    — request_approval()
  scheduler        — run_schedule() / MilestoneEngine.run()
  contract_valid   — validate_contract() per module
  team_lead_review — team_lead_review() per module
  planner_review   — planner_review_milestone()
  digital_twin     — MissionDigitalTwin.snapshot()
  quality_engine   — compute_quality_report()
  org_health       — OrgHealthTracker.generate_report()
  recovery         — AutonomousRecoveryEngine actions
  blocker_intel    — BlockerIntelligence.generate_report()
  ponytail_review  — _apply_ponytail_review()
  v5_wiring        — run_v5() non-scheduler overhead

Reports:
  useful_time_s    — time in scheduler / run_schedule (actual LLM work)
  overhead_time_s  — time in all orchestration layers
  overhead_pct     — orchestration_time / total_time * 100 (lower is better)
"""
from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SubsystemProfile:
    name: str
    calls: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0

    def is_useful_work(self) -> bool:
        """True for the subsystems that are the actual task execution
        (as opposed to orchestration overhead)."""
        return self.name in ("scheduler", "run_inner_loop", "agent_execution")


@dataclass
class OverheadReport:
    profiles: Dict[str, SubsystemProfile] = field(default_factory=dict)
    total_elapsed_s: float = 0.0

    @property
    def useful_time_s(self) -> float:
        return sum(
            p.total_ms / 1000 for p in self.profiles.values()
            if p.is_useful_work()
        )

    @property
    def overhead_time_s(self) -> float:
        return sum(
            p.total_ms / 1000 for p in self.profiles.values()
            if not p.is_useful_work()
        )

    @property
    def overhead_pct(self) -> float:
        t = self.total_elapsed_s
        return (self.overhead_time_s / t * 100) if t > 0 else 0.0

    def summary(self) -> str:
        lines = [
            f"Overhead profiler ({self.total_elapsed_s:.2f}s total):",
            f"  Useful work:       {self.useful_time_s:.3f}s",
            f"  Orchestration:     {self.overhead_time_s:.3f}s ({self.overhead_pct:.0f}%)",
        ]
        heavy = sorted(self.profiles.values(), key=lambda p: p.total_ms, reverse=True)[:5]
        for p in heavy:
            lines.append(f"  {p.name:<22} {p.calls}× {p.avg_ms:.1f}ms avg")
        return "\n".join(lines)

    def bottlenecks(self, threshold_ms: float = 50.0) -> List[str]:
        return [
            f"{p.name} ({p.avg_ms:.0f}ms avg, {p.calls} calls)"
            for p in self.profiles.values()
            if p.avg_ms >= threshold_ms and not p.is_useful_work()
        ]


class OverheadProfiler:
    """Lightweight context-manager based timer — wrap any subsystem call
    with `with profiler.measure("subsystem_name"):` and it records elapsed ms.

    Thread-safe (multiple milestone workers can profile concurrently).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._profiles: Dict[str, SubsystemProfile] = {}
        self._start = time.monotonic()

    @contextlib.contextmanager
    def measure(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            with self._lock:
                if name not in self._profiles:
                    self._profiles[name] = SubsystemProfile(name=name)
                p = self._profiles[name]
                p.calls += 1
                p.total_ms += elapsed_ms
                p.max_ms = max(p.max_ms, elapsed_ms)

    def record(self, name: str, elapsed_ms: float) -> None:
        """Record a pre-measured duration (for code that can't use the CM)."""
        with self._lock:
            if name not in self._profiles:
                self._profiles[name] = SubsystemProfile(name=name)
            p = self._profiles[name]
            p.calls += 1
            p.total_ms += elapsed_ms
            p.max_ms = max(p.max_ms, elapsed_ms)

    def report(self) -> OverheadReport:
        with self._lock:
            return OverheadReport(
                profiles=dict(self._profiles),
                total_elapsed_s=time.monotonic() - self._start,
            )

    def reset(self) -> None:
        with self._lock:
            self._profiles.clear()
            self._start = time.monotonic()
