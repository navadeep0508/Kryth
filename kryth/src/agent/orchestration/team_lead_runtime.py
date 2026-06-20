"""TeamLeadRuntime — V5 Phase 1.

Implements the full organizational hierarchy:

    Planner (CEO)
    ↓
    Program Manager  ← TeamLeadRuntime orchestrates this layer
    ↓
    Team Leads       ← one per milestone
    ↓
    Workers

Responsibilities per Team Lead:
  * Receive milestone from Program Manager
  * Generate fine-grained worker contracts
  * Assign ownership (no file overlap)
  * Track deliverables per worker
  * Track and broadcast blockers
  * Review each worker's output independently
  * Approve worker completion (workers CANNOT self-approve)
  * Escalate unresolvable issues to Program Manager

Additive: does not replace MilestoneEngine or Scheduler.
Plugs in as a pre/post-execution layer around each milestone run.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from agent.orchestration.milestone_engine import (
    ContractValidationResult,
    TeamLeadReviewResult,
    team_lead_review,
    validate_contract,
)


# ── Enums & data classes ──────────────────────────────────────────────────────

class WorkerStatus(Enum):
    PENDING    = "pending"
    ASSIGNED   = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW     = "review"          # awaiting team lead review
    APPROVED   = "approved"
    REWORK     = "rework"
    BLOCKED    = "blocked"
    FAILED     = "failed"


class EscalationLevel(Enum):
    NONE       = "none"
    TEAM_LEAD  = "team_lead"
    PROGRAM_MGR = "program_manager"
    PLANNER    = "planner"


@dataclass
class WorkerAssignment:
    worker_id: str
    role: str
    module_name: str
    contract_brief: str
    owned_files: List[str] = field(default_factory=list)
    owned_dirs: List[str] = field(default_factory=list)
    deliverables: List[str] = field(default_factory=list)
    status: WorkerStatus = WorkerStatus.PENDING
    output: str = ""
    blockers: List[str] = field(default_factory=list)
    review_result: Optional[TeamLeadReviewResult] = None
    validation_result: Optional[ContractValidationResult] = None
    attempts: int = 0
    assigned_at: float = field(default_factory=time.monotonic)
    completed_at: float = 0.0

    @property
    def duration_s(self) -> float:
        if self.completed_at > 0:
            return self.completed_at - self.assigned_at
        return time.monotonic() - self.assigned_at


@dataclass
class TeamLeadReport:
    """Summary produced by a Team Lead after finishing a milestone."""
    milestone_name: str
    team_lead_id: str
    approved_workers: List[str] = field(default_factory=list)
    rejected_workers: List[str] = field(default_factory=list)
    blocked_workers: List[str] = field(default_factory=list)
    escalations: List[str] = field(default_factory=list)
    deliverables_approved: List[str] = field(default_factory=list)
    deliverables_missing: List[str] = field(default_factory=list)
    blocker_summary: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    notes: str = ""

    @property
    def all_approved(self) -> bool:
        return not self.rejected_workers and not self.blocked_workers

    @property
    def approval_rate(self) -> float:
        total = len(self.approved_workers) + len(self.rejected_workers)
        return len(self.approved_workers) / total if total else 0.0


@dataclass
class ProgramManagerReport:
    """Aggregated report across all milestones from the Program Manager."""
    mission_name: str
    milestone_reports: List[TeamLeadReport] = field(default_factory=list)
    escalated_issues: List[str] = field(default_factory=list)
    overall_approval_rate: float = 0.0
    blocked_count: int = 0
    total_deliverables: int = 0
    approved_deliverables: int = 0
    elapsed_s: float = 0.0

    def summary(self) -> str:
        lines = [
            f"Program Manager Report: {self.mission_name}",
            f"  Milestones:  {len(self.milestone_reports)}",
            f"  Approval rate: {self.overall_approval_rate:.0%}",
            f"  Deliverables: {self.approved_deliverables}/{self.total_deliverables}",
            f"  Blocked:     {self.blocked_count}",
        ]
        if self.escalated_issues:
            lines.append(f"  Escalations: {len(self.escalated_issues)}")
            for iss in self.escalated_issues[:3]:
                lines.append(f"    ✗ {iss[:60]}")
        return "\n".join(lines)


# ── Team Lead ─────────────────────────────────────────────────────────────────

class TeamLead:
    """Manages workers for a single milestone.

    A Team Lead never approves work blindly — every worker output goes through:
      1. validate_contract()   — heuristic + file checks
      2. team_lead_review()    — independent second gate (different criteria)
      3. Only then: APPROVED

    If a worker is rejected, the Team Lead either requests rework (attempts < 2)
    or escalates to the Program Manager.
    """

    MAX_REWORK_ATTEMPTS = 2

    def __init__(self, milestone_name: str, lead_id: str = "") -> None:
        self.milestone_name = milestone_name
        self.lead_id = lead_id or f"lead_{milestone_name[:12].lower().replace(' ', '_')}"
        self._assignments: Dict[str, WorkerAssignment] = {}

    def assign_worker(
        self,
        worker_id: str,
        role: str,
        module_name: str,
        contract,          # DeliverableContract
        owned_files: List[str],
        owned_dirs: List[str],
    ) -> WorkerAssignment:
        """Assign a worker to a module with a specific contract."""
        brief = contract.to_worker_brief() if hasattr(contract, "to_worker_brief") else str(contract)
        asgn = WorkerAssignment(
            worker_id=worker_id,
            role=role,
            module_name=module_name,
            contract_brief=brief,
            owned_files=owned_files,
            owned_dirs=owned_dirs,
            deliverables=list(getattr(contract, "outputs", [])),
        )
        self._assignments[worker_id] = asgn
        return asgn

    def record_output(
        self,
        worker_id: str,
        output: str,
        contract,
        project_root: str = ".",
    ) -> Tuple[bool, str]:
        """Record a worker's output and run the two-gate review pipeline.

        Returns (approved: bool, notes: str).
        Workers CANNOT self-approve — this method is the only approval path.
        """
        asgn = self._assignments.get(worker_id)
        if asgn is None:
            return False, f"Unknown worker: {worker_id}"

        asgn.output = output
        asgn.attempts += 1
        asgn.status = WorkerStatus.REVIEW

        # Gate 1: Contract / deliverable validation
        val = validate_contract(contract, output, project_root)
        asgn.validation_result = val

        # Gate 2: Team Lead independent review
        tl = team_lead_review(contract, val, output)
        asgn.review_result = tl

        if val.passed and tl.approved:
            asgn.status = WorkerStatus.APPROVED
            asgn.completed_at = time.monotonic()
            return True, f"Approved: {val.notes}"

        # Not approved — decide: rework or escalate
        reason = "; ".join(val.failures[:2]) if val.failures else tl.notes
        if asgn.attempts < self.MAX_REWORK_ATTEMPTS:
            asgn.status = WorkerStatus.REWORK
            return False, f"Rework requested ({asgn.attempts}/{self.MAX_REWORK_ATTEMPTS}): {reason}"

        asgn.status = WorkerStatus.FAILED
        return False, f"Escalated after {asgn.attempts} attempts: {reason}"

    def record_blocker(self, worker_id: str, blocker: str) -> None:
        asgn = self._assignments.get(worker_id)
        if asgn:
            asgn.blockers.append(blocker)
            asgn.status = WorkerStatus.BLOCKED

    def generate_report(self) -> TeamLeadReport:
        report = TeamLeadReport(
            milestone_name=self.milestone_name,
            team_lead_id=self.lead_id,
        )
        for asgn in self._assignments.values():
            if asgn.status == WorkerStatus.APPROVED:
                report.approved_workers.append(asgn.worker_id)
                report.deliverables_approved.extend(asgn.deliverables)
            elif asgn.status == WorkerStatus.BLOCKED:
                report.blocked_workers.append(asgn.worker_id)
                report.blocker_summary.extend(asgn.blockers[:2])
            elif asgn.status in (WorkerStatus.FAILED, WorkerStatus.REWORK):
                report.rejected_workers.append(asgn.worker_id)
                if asgn.attempts >= self.MAX_REWORK_ATTEMPTS:
                    issue = f"{asgn.module_name}: {asgn.review_result.notes if asgn.review_result else 'failed'}"
                    report.escalations.append(issue)

        # Find missing deliverables
        approved_set = set(report.deliverables_approved)
        for asgn in self._assignments.values():
            for d in asgn.deliverables:
                if d not in approved_set:
                    report.deliverables_missing.append(d)

        report.elapsed_s = max(
            (a.duration_s for a in self._assignments.values()), default=0.0
        )
        return report

    @property
    def assignments(self) -> Dict[str, WorkerAssignment]:
        return dict(self._assignments)


# ── Program Manager ───────────────────────────────────────────────────────────

class ProgramManager:
    """Coordinates Team Leads across all milestones for a mission.

    Receives milestone results from MilestoneEngine callbacks, creates
    a TeamLead per milestone, processes worker outputs through the full
    approval chain, and aggregates a ProgramManagerReport.

    Usage (from MilestoneEngine or orchestration/__init__.py):

        pm = ProgramManager(mission_name="My App")

        # For each milestone execution result:
        report = pm.process_milestone(
            milestone_name="Milestone 1 — Auth",
            worker_outputs={"auth": "... AGENT_COMPLETE"},
            contracts={"auth": contract_obj},
            project_root=".",
        )
    """

    def __init__(self, mission_name: str) -> None:
        self.mission_name = mission_name
        self._leads: Dict[str, TeamLead] = {}
        self._reports: List[TeamLeadReport] = []
        self._escalated: List[str] = []
        self._start = time.monotonic()

    def process_milestone(
        self,
        milestone_name: str,
        worker_outputs: Dict[str, str],      # module_name → output
        contracts: Dict[str, object],         # module_name → DeliverableContract
        project_root: str = ".",
    ) -> TeamLeadReport:
        """Run the full Team Lead review pipeline for one milestone.

        Every worker output is reviewed independently — no self-approval.
        Returns the TeamLeadReport for this milestone.
        """
        lead = TeamLead(milestone_name)
        self._leads[milestone_name] = lead

        # Assign all workers
        for mod_name, contract in contracts.items():
            owned_files = list(getattr(contract, "files_to_create", []))
            owned_dirs  = [
                f.rstrip("/") for f in getattr(contract, "files_to_create", [])
                if f.endswith("/")
            ]
            lead.assign_worker(
                worker_id=mod_name,
                role=f"{mod_name} Team",
                module_name=mod_name,
                contract=contract,
                owned_files=owned_files,
                owned_dirs=owned_dirs,
            )

        # Process each worker output through the review gates
        for mod_name, output in worker_outputs.items():
            contract = contracts.get(mod_name)
            if contract is None:
                continue

            # Detect blocker language before attempting review
            low = (output or "").lower()
            if any(m in low for m in ("blocked by", "waiting for", "depends on")):
                lead.record_blocker(mod_name, f"Worker reports dependency block: {output[:80]}")
                continue

            approved, notes = lead.record_output(
                worker_id=mod_name,
                output=output,
                contract=contract,
                project_root=project_root,
            )

            if not approved:
                asgn = lead.assignments.get(mod_name)
                if asgn and asgn.attempts >= TeamLead.MAX_REWORK_ATTEMPTS:
                    self._escalated.append(
                        f"{milestone_name}/{mod_name}: {notes[:80]}"
                    )

        report = lead.generate_report()
        self._reports.append(report)
        return report

    def final_report(self) -> ProgramManagerReport:
        all_approved = sum(len(r.approved_workers) for r in self._reports)
        all_rejected = sum(len(r.rejected_workers) for r in self._reports)
        all_total    = all_approved + all_rejected
        all_delivs   = sum(len(r.deliverables_approved) + len(r.deliverables_missing)
                           for r in self._reports)
        appr_delivs  = sum(len(r.deliverables_approved) for r in self._reports)

        return ProgramManagerReport(
            mission_name=self.mission_name,
            milestone_reports=list(self._reports),
            escalated_issues=list(self._escalated),
            overall_approval_rate=all_approved / all_total if all_total else 0.0,
            blocked_count=sum(len(r.blocked_workers) for r in self._reports),
            total_deliverables=all_delivs,
            approved_deliverables=appr_delivs,
            elapsed_s=time.monotonic() - self._start,
        )

    @property
    def leads(self) -> Dict[str, TeamLead]:
        return dict(self._leads)
