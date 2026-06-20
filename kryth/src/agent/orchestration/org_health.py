"""OrgHealth — V5 Phase 7.

Tracks organizational health metrics across workers, leads, and departments:

  * Worker utilization (active / total time)
  * Lead utilization
  * Department throughput
  * Idle time, blocked time, review time
  * Approval latency (time from completion to approval)
  * Mission throughput (deliverables / hour)
  * Bottleneck detection

Produces OrgHealthReport with actionable insights:
  * Overloaded teams
  * Idle teams
  * Bottlenecks (review gates, slow workers)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class HealthStatus(Enum):
    HEALTHY    = "healthy"
    DEGRADED   = "degraded"
    OVERLOADED = "overloaded"
    IDLE       = "idle"


@dataclass
class WorkerHealthRecord:
    worker_id: str
    role: str
    active_s: float = 0.0
    idle_s: float = 0.0
    blocked_s: float = 0.0
    review_s: float = 0.0
    deliverables_produced: int = 0
    approvals_received: int = 0
    rework_count: int = 0
    approval_latency_s: float = 0.0  # avg time from done → approved

    @property
    def total_s(self) -> float:
        return self.active_s + self.idle_s + self.blocked_s + self.review_s

    @property
    def utilization(self) -> float:
        t = self.total_s
        return self.active_s / t if t > 0 else 0.0

    @property
    def blocked_pct(self) -> float:
        t = self.total_s
        return self.blocked_s / t if t > 0 else 0.0

    @property
    def status(self) -> HealthStatus:
        if self.utilization > 0.90:
            return HealthStatus.OVERLOADED
        if self.utilization < 0.10 and self.total_s > 10:
            return HealthStatus.IDLE
        if self.blocked_pct > 0.40:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY


@dataclass
class LeadHealthRecord:
    lead_id: str
    milestone_name: str
    workers_managed: int = 0
    approvals_given: int = 0
    rejections_given: int = 0
    avg_review_latency_s: float = 0.0
    escalations: int = 0

    @property
    def approval_rate(self) -> float:
        total = self.approvals_given + self.rejections_given
        return self.approvals_given / total if total else 0.0

    @property
    def status(self) -> HealthStatus:
        if self.avg_review_latency_s > 300:
            return HealthStatus.DEGRADED
        if self.escalations > 2:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY


@dataclass
class OrgHealthReport:
    mission_name: str
    worker_records: List[WorkerHealthRecord] = field(default_factory=list)
    lead_records: List[LeadHealthRecord] = field(default_factory=list)
    overloaded_teams: List[str] = field(default_factory=list)
    idle_teams: List[str] = field(default_factory=list)
    bottlenecks: List[str] = field(default_factory=list)
    mission_throughput: float = 0.0    # deliverables per hour
    avg_approval_latency_s: float = 0.0
    overall_utilization: float = 0.0
    elapsed_s: float = 0.0

    def summary(self) -> str:
        lines = [
            f"Org Health: {self.mission_name}",
            f"  Overall utilization: {self.overall_utilization:.0%}",
            f"  Mission throughput:  {self.mission_throughput:.1f} deliverables/hr",
            f"  Avg approval latency: {self.avg_approval_latency_s:.1f}s",
        ]
        if self.overloaded_teams:
            lines.append(f"  Overloaded: {', '.join(self.overloaded_teams[:4])}")
        if self.idle_teams:
            lines.append(f"  Idle: {', '.join(self.idle_teams[:4])}")
        if self.bottlenecks:
            lines.append(f"  Bottlenecks: {', '.join(self.bottlenecks[:3])}")
        return "\n".join(lines)


class OrgHealthTracker:
    """Accumulates worker/lead telemetry and produces health reports."""

    def __init__(self, mission_name: str) -> None:
        self.mission_name = mission_name
        self._workers: Dict[str, WorkerHealthRecord] = {}
        self._leads: Dict[str, LeadHealthRecord] = {}
        self._start = time.monotonic()
        self._total_deliverables = 0

    # ── Worker recording ──────────────────────────────────────────────────────

    def record_worker(
        self,
        worker_id: str,
        role: str,
        active_s: float,
        idle_s: float = 0.0,
        blocked_s: float = 0.0,
        review_s: float = 0.0,
        deliverables: int = 0,
        approvals: int = 0,
        rework: int = 0,
        approval_latency_s: float = 0.0,
    ) -> WorkerHealthRecord:
        rec = WorkerHealthRecord(
            worker_id=worker_id,
            role=role,
            active_s=active_s,
            idle_s=idle_s,
            blocked_s=blocked_s,
            review_s=review_s,
            deliverables_produced=deliverables,
            approvals_received=approvals,
            rework_count=rework,
            approval_latency_s=approval_latency_s,
        )
        self._workers[worker_id] = rec
        self._total_deliverables += deliverables
        return rec

    def record_lead(
        self,
        lead_id: str,
        milestone_name: str,
        workers_managed: int,
        approvals: int,
        rejections: int,
        avg_review_latency_s: float = 0.0,
        escalations: int = 0,
    ) -> LeadHealthRecord:
        rec = LeadHealthRecord(
            lead_id=lead_id,
            milestone_name=milestone_name,
            workers_managed=workers_managed,
            approvals_given=approvals,
            rejections_given=rejections,
            avg_review_latency_s=avg_review_latency_s,
            escalations=escalations,
        )
        self._leads[lead_id] = rec
        return rec

    # ── From MilestoneResult ──────────────────────────────────────────────────

    def ingest_milestone_result(self, ms_result) -> None:
        """Ingest a MilestoneResult from MilestoneEngine automatically."""
        elapsed = getattr(ms_result, "elapsed_s", 0.0)
        # modules_run holds AgentRole.role strings (e.g. "Database Team");
        # worker_outputs / team_lead_reviews are keyed by the bare module
        # name (e.g. "Database") — strip the suffix to look those up.
        mods    = getattr(ms_result, "modules_run", [])
        outputs = getattr(ms_result, "worker_outputs", {})
        tl_rev  = getattr(ms_result, "team_lead_reviews", {})
        n_mods  = max(len(mods), 1)

        for role in mods:
            bare = role[:-5] if role.endswith(" Team") else role
            approved = bool(getattr(tl_rev.get(bare), "approved", False)) if tl_rev else False
            out      = outputs.get(bare, "")
            # Heuristic timing: split elapsed evenly across workers
            active = elapsed / n_mods if elapsed else 1.0
            self.record_worker(
                worker_id=bare,
                role=role,
                active_s=active,
                deliverables=1 if approved else 0,
                approvals=1 if approved else 0,
            )

        # Ingest lead record
        ms_name  = getattr(ms_result, "milestone_name", "unknown")
        approved = getattr(ms_result, "approved", False)
        n_appr   = sum(1 for r in tl_rev.values() if getattr(r, "approved", False))
        n_rej    = len(mods) - n_appr
        self.record_lead(
            lead_id=f"lead_{ms_name[:12]}",
            milestone_name=ms_name,
            workers_managed=len(mods),
            approvals=n_appr,
            rejections=n_rej,
            avg_review_latency_s=elapsed * 0.15,
        )

    def generate_report(self) -> OrgHealthReport:
        elapsed = time.monotonic() - self._start
        workers = list(self._workers.values())
        leads   = list(self._leads.values())

        overloaded = [w.role for w in workers if w.status == HealthStatus.OVERLOADED]
        idle       = [w.role for w in workers if w.status == HealthStatus.IDLE]

        # Bottlenecks: leads with high avg review latency or high rejection rate
        bottlenecks: List[str] = []
        for lead in leads:
            if lead.avg_review_latency_s > 120 or lead.approval_rate < 0.5:
                bottlenecks.append(lead.milestone_name)

        avg_util = (
            sum(w.utilization for w in workers) / len(workers) if workers else 0.0
        )
        avg_lat  = (
            sum(w.approval_latency_s for w in workers) / len(workers) if workers else 0.0
        )
        # Floor elapsed at 1s — avoids a nonsensical magnitude when this is
        # called moments after the mission started (e.g. synthetic tests).
        throughput = (self._total_deliverables / max(elapsed, 1.0) * 3600) if elapsed > 0 else 0.0

        return OrgHealthReport(
            mission_name=self.mission_name,
            worker_records=workers,
            lead_records=leads,
            overloaded_teams=overloaded,
            idle_teams=idle,
            bottlenecks=bottlenecks,
            mission_throughput=throughput,
            avg_approval_latency_s=avg_lat,
            overall_utilization=avg_util,
            elapsed_s=elapsed,
        )
