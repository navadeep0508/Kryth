"""Portfolio Execution Engine — V5 Phase 8.

Manages multiple simultaneous missions sharing providers, workers, and budgets.

Supports:
  * Mission A, B, C, D running concurrently
  * Shared provider pool (ProviderHealth across missions)
  * Shared token budget
  * Portfolio health dashboard
  * Portfolio risk assessment
  * Portfolio throughput metrics

Additive: does not change single-mission execution.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class MissionState(Enum):
    QUEUED    = "queued"
    RUNNING   = "running"
    PAUSED    = "paused"
    COMPLETED = "completed"
    FAILED    = "failed"


class PortfolioRisk(Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


@dataclass
class MissionEntry:
    mission_id: str
    name: str
    state: MissionState = MissionState.QUEUED
    priority: int = 5              # 1=highest, 10=lowest
    token_budget: int = 500_000
    tokens_used: int = 0
    deliverables_planned: int = 0
    deliverables_completed: int = 0
    milestones_total: int = 0
    milestones_done: int = 0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    risk: PortfolioRisk = PortfolioRisk.LOW
    notes: str = ""

    @property
    def progress_pct(self) -> float:
        if self.milestones_total == 0:
            return 100.0 if self.state == MissionState.COMPLETED else 0.0
        return self.milestones_done / self.milestones_total * 100

    @property
    def elapsed_s(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.completed_at or time.monotonic()
        return end - self.started_at

    @property
    def tokens_remaining(self) -> int:
        return max(0, self.token_budget - self.tokens_used)


@dataclass
class PortfolioHealthReport:
    total_missions: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    queued: int = 0
    total_tokens_used: int = 0
    total_tokens_budget: int = 0
    overall_risk: PortfolioRisk = PortfolioRisk.LOW
    throughput_deliverables_hr: float = 0.0
    bottleneck_missions: List[str] = field(default_factory=list)
    at_risk_missions: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def summary(self) -> str:
        lines = [
            f"Portfolio: {self.total_missions} missions",
            f"  Running: {self.running}  Completed: {self.completed}  "
            f"Failed: {self.failed}  Queued: {self.queued}",
            f"  Tokens: {self.total_tokens_used:,}/{self.total_tokens_budget:,}",
            f"  Throughput: {self.throughput_deliverables_hr:.1f} deliverables/hr",
            f"  Risk: {self.overall_risk.value.upper()}",
        ]
        if self.at_risk_missions:
            lines.append(f"  At risk: {', '.join(self.at_risk_missions[:4])}")
        if self.bottleneck_missions:
            lines.append(f"  Bottlenecks: {', '.join(self.bottleneck_missions[:3])}")
        return "\n".join(lines)


class PortfolioManager:
    """Manages a portfolio of concurrent missions.

    Usage:
        pm = PortfolioManager(total_token_budget=2_000_000)
        pm.register("crud_api",  "CRUD API",  token_budget=400_000)
        pm.register("jwt_auth",  "JWT Auth",  token_budget=200_000)
        pm.start("crud_api")
        pm.update("crud_api", tokens_used=50_000, milestones_done=2)
        pm.complete("crud_api")
        report = pm.health_report()
    """

    def __init__(self, total_token_budget: int = 2_000_000) -> None:
        self._missions: Dict[str, MissionEntry] = {}
        self._total_budget = total_token_budget
        self._start = time.monotonic()

    def register(
        self,
        mission_id: str,
        name: str,
        token_budget: int = 500_000,
        priority: int = 5,
        deliverables_planned: int = 0,
        milestones_total: int = 0,
    ) -> MissionEntry:
        entry = MissionEntry(
            mission_id=mission_id,
            name=name,
            token_budget=min(token_budget, self._total_budget),
            priority=priority,
            deliverables_planned=deliverables_planned,
            milestones_total=milestones_total,
        )
        self._missions[mission_id] = entry
        return entry

    def start(self, mission_id: str) -> bool:
        entry = self._missions.get(mission_id)
        if entry is None:
            return False
        entry.state = MissionState.RUNNING
        entry.started_at = time.monotonic()
        return True

    def update(
        self,
        mission_id: str,
        *,
        tokens_used: int = 0,
        milestones_done: int = 0,
        deliverables_completed: int = 0,
        risk: Optional[PortfolioRisk] = None,
        notes: str = "",
    ) -> bool:
        entry = self._missions.get(mission_id)
        if entry is None:
            return False
        if tokens_used:
            entry.tokens_used += tokens_used
        if milestones_done:
            entry.milestones_done = milestones_done
        if deliverables_completed:
            entry.deliverables_completed = deliverables_completed
        if risk is not None:
            entry.risk = risk
        if notes:
            entry.notes = notes
        return True

    def complete(self, mission_id: str, success: bool = True) -> bool:
        entry = self._missions.get(mission_id)
        if entry is None:
            return False
        entry.state = MissionState.COMPLETED if success else MissionState.FAILED
        entry.completed_at = time.monotonic()
        return True

    def pause(self, mission_id: str) -> bool:
        entry = self._missions.get(mission_id)
        if entry and entry.state == MissionState.RUNNING:
            entry.state = MissionState.PAUSED
            return True
        return False

    def health_report(self) -> PortfolioHealthReport:
        missions = list(self._missions.values())
        elapsed  = time.monotonic() - self._start

        running   = [m for m in missions if m.state == MissionState.RUNNING]
        completed = [m for m in missions if m.state == MissionState.COMPLETED]
        failed    = [m for m in missions if m.state == MissionState.FAILED]
        queued    = [m for m in missions if m.state == MissionState.QUEUED]

        total_tokens = sum(m.tokens_used for m in missions)
        total_budget = sum(m.token_budget for m in missions)
        total_delivs = sum(m.deliverables_completed for m in missions)
        # Floor elapsed at 1s — a sub-second elapsed (e.g. right after register())
        # would otherwise blow throughput up to a nonsensical magnitude.
        throughput   = (total_delivs / max(elapsed, 1.0) * 3600) if elapsed > 0 else 0.0

        # Risk: worst among running missions
        running_risks = [m.risk for m in running]
        overall_risk  = max(running_risks, key=lambda r: r.value, default=PortfolioRisk.LOW)

        # Budget at risk: < 20% remaining
        at_risk = [m.name for m in running if m.tokens_remaining < m.token_budget * 0.2]

        # Bottlenecks: slow progress (< 30% done past halfway through budget)
        bottlenecks = [
            m.name for m in running
            if m.tokens_used > m.token_budget * 0.5 and m.progress_pct < 30
        ]

        return PortfolioHealthReport(
            total_missions=len(missions),
            running=len(running),
            completed=len(completed),
            failed=len(failed),
            queued=len(queued),
            total_tokens_used=total_tokens,
            total_tokens_budget=total_budget,
            overall_risk=overall_risk,
            throughput_deliverables_hr=throughput,
            bottleneck_missions=bottlenecks,
            at_risk_missions=at_risk,
            elapsed_s=elapsed,
        )

    def missions(self) -> List[MissionEntry]:
        return list(self._missions.values())

    def get(self, mission_id: str) -> Optional[MissionEntry]:
        return self._missions.get(mission_id)
