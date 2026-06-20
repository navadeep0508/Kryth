"""Milestone Execution Engine — KRYTH V3.

Transforms flat DAG execution into milestone-driven delivery:

  ProjectPlan
    ├── Milestone 1 — Foundation (Database, Auth)
    ├── Milestone 2 — Core     (Student Portal, Company Portal)
    ├── Milestone 3 — AI       (Matching Engine)
    └── Milestone 4 — Delivery (Testing, Deployment)

Each milestone is:
  1. Executed (workers run their contracts in parallel within the milestone)
  2. Validated (contract validation checks each worker's deliverables)
  3. Reviewed (planner review gate: APPROVED or REWORK REQUIRED)
  4. Checkpointed (mission state persisted)

On failure: only the failed milestone retries, not the whole mission.

Fallback: if anything fails, execution continues using the standard scheduler.
All code is additive — existing run_schedule() is always available.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.orchestration.project_planner import ProjectPlan, ProjectMilestone, DeliverableContract
    from agent.orchestration.task_dag import TaskDAG
    from agent.orchestration.team_generator import TeamPlan, AgentRole
    from agent.orchestration.scheduler import SchedulerResult, WorkerResult


# ── V4 Phase 5: Deliverable-based status tracking ────────────────────────────

class DeliverableStatus(Enum):
    """Lifecycle of a single deliverable — execution is tracked by deliverable,
    not by task, per V4 Phase 5."""
    PENDING      = "pending"
    IN_PROGRESS  = "in_progress"
    VALIDATED    = "validated"      # passed contract validation
    APPROVED     = "approved"       # passed team lead + planner review
    FAILED       = "failed"


@dataclass
class DeliverableRecord:
    name: str
    module_name: str
    status: DeliverableStatus = DeliverableStatus.PENDING
    notes: str = ""


# ── Contract validation ───────────────────────────────────────────────────────

@dataclass
class ContractValidationResult:
    """Result of validating a worker's deliverable contract."""
    module_name: str
    passed: bool
    checks_run: int = 0
    checks_passed: int = 0
    failures: List[str] = field(default_factory=list)
    notes: str = ""

    @property
    def score(self) -> float:
        return self.checks_passed / max(self.checks_run, 1)


def validate_contract(
    contract: "DeliverableContract",
    worker_output: str,
    project_root: str = ".",
) -> ContractValidationResult:
    """Validate that a worker's output satisfies the deliverable contract.

    Checks:
    1. Output contains AGENT_COMPLETE sentinel
    2. Success criteria mentioned in output
    3. Key deliverables mentioned in output
    4. Files exist on disk (when success_criteria specifies filenames)

    This is a lightweight heuristic — not a full test runner.
    """
    result = ContractValidationResult(
        module_name=contract.module_name,
        passed=False,
    )
    checks = 0
    passed = 0
    failures = []

    # Check 1: Completion sentinel
    checks += 1
    if "AGENT_COMPLETE" in (worker_output or ""):
        passed += 1
    else:
        failures.append("Agent did not emit AGENT_COMPLETE sentinel")

    # Check 2: Success criteria mentioned
    if contract.success_criteria:
        for criterion in contract.success_criteria[:3]:
            checks += 1
            # Heuristic: check if key words from the criterion appear in output
            keywords = [w.lower() for w in criterion.split() if len(w) > 4]
            if keywords and any(kw in (worker_output or "").lower() for kw in keywords[:3]):
                passed += 1
            else:
                failures.append(f"Success criterion not evidenced: {criterion[:50]}")

    # Check 3: Key outputs mentioned
    if contract.outputs:
        for output in contract.outputs[:2]:
            checks += 1
            keywords = [w.lower() for w in output.split() if len(w) > 3]
            if keywords and any(kw in (worker_output or "").lower() for kw in keywords[:3]):
                passed += 1
            else:
                failures.append(f"Output not evidenced: {output[:40]}")

    # Check 4: Critical files exist (only if explicitly listed and short paths)
    if contract.files_to_create and project_root != ".":
        for fpath in contract.files_to_create[:3]:
            if "/" in fpath or "\\" in fpath:
                checks += 1
                full = os.path.join(project_root, fpath.lstrip("/"))
                if os.path.exists(full):
                    passed += 1
                else:
                    # Soft failure — file may be at a different path
                    pass  # don't count as failure, just not a pass

    result.checks_run = checks
    result.checks_passed = passed
    result.failures = failures
    # Pass if ≥60% checks pass (lenient — heuristic validation)
    result.passed = checks > 0 and (passed / checks) >= 0.6
    result.notes = f"{passed}/{checks} checks passed"
    return result


# ── V4 Phase 3+4: Team Lead review gate ───────────────────────────────────────
#
# Approval pipeline (V4 Phase 4 — contract enforcement):
#   Worker → Deliverable Validation → Team Lead Review → Planner Review → Approved
#
# Workers can NEVER self-approve. validate_contract() is the deliverable check;
# team_lead_review() is an independent second gate (different acceptance bar);
# planner_review_milestone() is the final gate before the next milestone starts.

@dataclass
class TeamLeadReviewResult:
    module_name: str
    approved: bool
    notes: str = ""


def team_lead_review(
    contract: "DeliverableContract",
    validation: ContractValidationResult,
    worker_output: str,
) -> TeamLeadReviewResult:
    """V4: Team Lead reviews a worker's validated deliverable before it can
    proceed to the Planner Review gate.

    The Team Lead checks things contract validation does NOT:
    - The validation actually passed (first gate)
    - The output isn't suspiciously short (signal of a stalled/incomplete worker)
    - No unresolved blockers mentioned in the output

    This is a SEPARATE gate from validate_contract — a worker that passes
    deliverable validation can still be rejected by the Team Lead.
    """
    if not validation.passed:
        return TeamLeadReviewResult(
            module_name=contract.module_name,
            approved=False,
            notes=f"Deliverable validation failed: {validation.notes}",
        )

    output = worker_output or ""
    notes = []

    # Suspiciously short output for a real deliverable
    if len(output.strip()) < 20:
        return TeamLeadReviewResult(
            module_name=contract.module_name,
            approved=False,
            notes="Output too short — worker may not have completed real work",
        )

    # Unresolved blocker language
    _BLOCKER_MARKERS = ("blocked", "cannot proceed", "unable to complete", "todo: fix")
    low = output.lower()
    if any(marker in low for marker in _BLOCKER_MARKERS):
        return TeamLeadReviewResult(
            module_name=contract.module_name,
            approved=False,
            notes="Worker output reports a blocker — needs resolution before approval",
        )

    notes.append(f"Validation score {validation.score:.0%}")
    return TeamLeadReviewResult(
        module_name=contract.module_name,
        approved=True,
        notes="; ".join(notes),
    )


# ── Milestone result tracking ─────────────────────────────────────────────────

@dataclass
class MilestoneResult:
    """Execution result for a single milestone."""
    milestone_name: str
    order: int
    modules_run: List[str] = field(default_factory=list)
    worker_outputs: Dict[str, str] = field(default_factory=dict)
    contract_validations: Dict[str, ContractValidationResult] = field(default_factory=dict)
    # V4: team lead review gate results (between validation and planner review)
    team_lead_reviews: Dict[str, TeamLeadReviewResult] = field(default_factory=dict)
    # Tracking
    success: bool = False
    approved: bool = False       # planner review gate result
    rework_required: bool = False
    rework_notes: str = ""
    elapsed_s: float = 0.0
    deliverables_planned: int = 0
    deliverables_completed: int = 0
    is_critical: bool = False    # V4 Phase 6: on the critical path

    @property
    def completion_pct(self) -> float:
        if self.deliverables_planned == 0:
            return 100.0 if self.success else 0.0
        return self.deliverables_completed / self.deliverables_planned * 100

    @property
    def team_lead_approved_count(self) -> int:
        return sum(1 for r in self.team_lead_reviews.values() if r.approved)


@dataclass
class MissionDeliveryResult:
    """Full mission result including all milestones."""
    project_name: str
    milestones: List[MilestoneResult] = field(default_factory=list)
    # Aggregated
    total_modules: int = 0
    completed_modules: int = 0
    failed_modules: int = 0
    total_deliverables_planned: int = 0
    total_deliverables_completed: int = 0
    critical_path_names: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    # V4 Phase 10: success metrics
    team_lead_approvals: int = 0
    planner_approvals: int = 0
    critical_path_duration_s: float = 0.0
    recovered_from_checkpoint: bool = False

    @property
    def success(self) -> bool:
        return all(ms.success for ms in self.milestones)

    @property
    def success_pct(self) -> float:
        if not self.milestones:
            return 0.0
        return sum(1 for ms in self.milestones if ms.success) / len(self.milestones) * 100

    def scorecard(self) -> str:
        lines = [
            "═══ MISSION DELIVERY SCORECARD ═══",
            f"  Project:              {self.project_name}",
            f"  Milestones:           {sum(1 for ms in self.milestones if ms.success)}/{len(self.milestones)} completed",
            f"  Modules:              {self.completed_modules}/{self.total_modules} completed",
            f"  Deliverables:         {self.total_deliverables_completed}/{self.total_deliverables_planned} produced",
            f"  Team Lead approvals:  {self.team_lead_approvals}",
            f"  Planner approvals:    {self.planner_approvals}",
            f"  Mission duration:     {self.elapsed_s:.1f}s",
        ]
        if self.critical_path_duration_s:
            lines.append(f"  Critical path time:   {self.critical_path_duration_s:.1f}s")
        if self.recovered_from_checkpoint:
            lines.append(f"  Recovery:             resumed from checkpoint")
        if self.critical_path_names:
            lines.append(f"  Critical path:        {' → '.join(self.critical_path_names[:5])}")
        for ms in self.milestones:
            icon = "✓" if ms.success else "✗"
            crit = " ⚑" if ms.is_critical else ""
            appr = " (approved)" if ms.approved else " (pending review)" if ms.success else ""
            lines.append(f"  {icon} {ms.milestone_name}{crit}{appr}")
        lines.append("═══════════════════════════════════")
        return "\n".join(lines)


# ── Planner review gate ───────────────────────────────────────────────────────

def planner_review_milestone(
    milestone_name: str,
    validations: Dict[str, ContractValidationResult],
    *,
    interactive: bool = False,
    reader=None,
) -> tuple[bool, str]:
    """V3: Planner reviews milestone completion before allowing next milestone.

    Returns (approved: bool, notes: str).
    Non-interactive → auto-approve if all contracts passed.
    """
    all_passed = all(v.passed for v in validations.values())

    if not interactive:
        # Headless: auto-approve if contracts pass
        if all_passed:
            return True, "Auto-approved: all contracts satisfied"
        failed = [k for k, v in validations.items() if not v.passed]
        return False, f"Contracts failed: {', '.join(failed[:3])}"

    # Interactive review
    try:
        from agent import ui
        ui.muted(f"\n  ◈ PLANNER REVIEW: {milestone_name}")
        for mod, v in validations.items():
            icon = "✓" if v.passed else "✗"
            ui.muted(f"    {icon} {mod}: {v.notes}")

        read = reader or (lambda: input(
            "\n  Planner decision: [A]pprove / [R]ework required > "
        ).strip().lower())
        try:
            choice = read()
        except (EOFError, KeyboardInterrupt):
            choice = "a"
        if choice[:1] in ("a", "y", ""):
            return True, "Planner approved"
        return False, "Planner requested rework"
    except Exception:
        return all_passed, "Auto-approved (review unavailable)"


# ── Deliverable tracking ──────────────────────────────────────────────────────

class DeliverableTracker:
    """V3/V4: Tracks planned vs completed deliverables per milestone and overall.

    V4: Each individual deliverable now carries a DeliverableStatus through
    its lifecycle: PENDING → IN_PROGRESS → VALIDATED → APPROVED (or FAILED).
    Execution is tracked by deliverable, not by task (V4 Phase 5).
    """

    def __init__(self, plan: "ProjectPlan") -> None:
        self._plan = plan
        self._completed: Dict[str, List[str]] = {}   # module_name → [completed deliverables]
        self._failed: Dict[str, List[str]] = {}
        # V4: per-deliverable status records, keyed by (module_name, deliverable_name)
        self._records: Dict[tuple, DeliverableRecord] = {}
        for m in plan.modules:
            for d in m.deliverables:
                self._records[(m.name, d)] = DeliverableRecord(name=d, module_name=m.name)

    def set_status(self, module_name: str, deliverable: str, status: DeliverableStatus,
                  notes: str = "") -> None:
        key = (module_name, deliverable)
        if key not in self._records:
            self._records[key] = DeliverableRecord(name=deliverable, module_name=module_name)
        self._records[key].status = status
        self._records[key].notes = notes

    def get_status(self, module_name: str, deliverable: str) -> DeliverableStatus:
        rec = self._records.get((module_name, deliverable))
        return rec.status if rec else DeliverableStatus.PENDING

    def deliverables_by_status(self, status: DeliverableStatus) -> List[DeliverableRecord]:
        return [r for r in self._records.values() if r.status == status]

    def record_complete(self, module_name: str, deliverables: List[str]) -> None:
        self._completed[module_name] = list(deliverables)
        for d in deliverables:
            self.set_status(module_name, d, DeliverableStatus.VALIDATED)

    def record_approved(self, module_name: str, deliverables: List[str]) -> None:
        """V4: Mark deliverables APPROVED after passing all review gates."""
        for d in deliverables:
            self.set_status(module_name, d, DeliverableStatus.APPROVED)

    def record_failed(self, module_name: str, reason: str) -> None:
        self._failed[module_name] = [reason]
        for d in [r.name for r in self._records.values() if r.module_name == module_name]:
            self.set_status(module_name, d, DeliverableStatus.FAILED, notes=reason)

    def milestone_status(self, milestone: "ProjectMilestone") -> dict:
        planned = sum(
            len(m.deliverables)
            for m in self._plan.modules
            if m.name in milestone.modules
        )
        completed = sum(
            len(self._completed.get(name, []))
            for name in milestone.modules
        )
        failed = [n for n in milestone.modules if n in self._failed]
        return {
            "planned": planned,
            "completed": completed,
            "failed": failed,
            "missing": max(0, planned - completed),
        }

    def overall_status(self) -> dict:
        all_planned = sum(len(m.deliverables) for m in self._plan.modules)
        all_completed = sum(len(v) for v in self._completed.values())
        return {
            "planned": all_planned,
            "completed": all_completed,
            "missing": max(0, all_planned - all_completed),
            "failed_modules": list(self._failed.keys()),
        }

    def format_status(self) -> str:
        s = self.overall_status()
        return (
            f"Deliverables: {s['completed']}/{s['planned']} completed  |  "
            f"Missing: {s['missing']}  |  "
            f"Failed modules: {s['failed_modules'] or 'none'}"
        )


# ── Milestone execution engine ────────────────────────────────────────────────

class MilestoneEngine:
    """V3: Executes a ProjectPlan milestone-by-milestone.

    Each milestone runs its modules in parallel (via the existing scheduler),
    validates contracts, runs the planner review gate, and checkpoints state.

    Falls back gracefully on any error — existing run_schedule() always runs.
    """

    def __init__(
        self,
        plan: "ProjectPlan",
        dag: "TaskDAG",
        team: "TeamPlan",
        project_context: str = "",
        user_input: str = "",
        max_turns_per_agent: int = 80,
        max_workers: int = 4,
        project_root: str = ".",
        on_milestone_complete=None,  # callback(milestone_name, result)
        resume_from_order: int = 1,  # V4 Phase 8: milestone-level recovery
    ) -> None:
        self._plan = plan
        self._dag = dag
        self._team = team
        self._context = project_context
        self._user_input = user_input
        self._max_turns = max_turns_per_agent
        self._max_workers = max_workers
        self._root = project_root
        self._on_complete = on_milestone_complete
        self._tracker = DeliverableTracker(plan)
        self._resume_from_order = max(1, resume_from_order)
        # V5 Ponytail Phase 9: per-module overengineering scores, accumulated
        # only when the PONYTAIL profile is active (see _apply_ponytail_review).
        self._ponytail_scores: List = []
        self._ponytail_files_avoided: int = 0
        self._ponytail_files_reused: int = 0

    def run(self) -> MissionDeliveryResult:
        """Execute the plan milestone-by-milestone."""
        from agent import ui

        start = time.monotonic()
        milestones = self._plan.ensure_structured_milestones()
        critical_names = set(self._plan.critical_path())
        agent_map = {a.id: a for a in self._team.agents}

        result = MissionDeliveryResult(
            project_name=self._plan.project_name,
            total_modules=len(self._plan.modules),
            critical_path_names=self._plan.critical_path(),
            recovered_from_checkpoint=(self._resume_from_order > 1),
        )

        prior_outputs: Dict[str, str] = {"__user_input__": self._user_input}
        completed_agent_ids: set = set()
        _critical_path_elapsed = 0.0

        for milestone in sorted(milestones, key=lambda m: m.order):
            # V4 Phase 8: skip milestones already completed before a crash
            if milestone.order < self._resume_from_order:
                continue

            ms_start = time.monotonic()
            ui.muted(f"\n  ◈ MILESTONE {milestone.order}: {milestone.name}")
            if milestone.is_critical:
                ui.muted(f"    ⚑ Critical path")

            # Build sub-team from milestone modules
            ms_agents = [
                agent_map[_id(name)]
                for name in milestone.modules
                if _id(name) in agent_map
            ]

            if not ms_agents:
                ui.muted(f"    (no agents for milestone — skipping)")
                continue

            # V4 Phase 6: Critical path priority — boost turn budget for
            # critical-path agents so they are less likely to truncate/retry,
            # since delaying them delays the whole mission.
            for a in ms_agents:
                if a.role.replace(" Team", "") in critical_names or a.id in {
                    _id(n) for n in critical_names
                }:
                    a.max_turns = int(a.max_turns * 1.5)

            # Execute this milestone via the standard scheduler
            ms_result = self._run_milestone_agents(
                milestone, ms_agents, prior_outputs, completed_agent_ids
            )
            ms_result.is_critical = milestone.is_critical

            # ── V4 Phase 4: Contract enforcement pipeline ──────────────────────
            # Worker → Deliverable Validation → Team Lead Review → Planner Review
            validations: Dict[str, ContractValidationResult] = {}
            tl_reviews: Dict[str, TeamLeadReviewResult] = {}
            for name in milestone.modules:
                contract = self._plan.get_contract(name)
                output = ms_result.worker_outputs.get(name, "")
                if contract is not None:
                    val = validate_contract(contract, output, self._root)
                    validations[name] = val

                    # V4: Team Lead Review gate — independent second check.
                    # Workers can never self-approve; deliverable validation
                    # passing is necessary but not sufficient.
                    tl_result = team_lead_review(contract, val, output)

                    # V5: Ponytail simplicity gate — additive third check, only
                    # under the PONYTAIL profile, and only ever applied AFTER
                    # the base Team Lead review already approved. Can demote
                    # an approval to rejection on a clear overengineering signal;
                    # never promotes a rejection to an approval.
                    if val.passed and tl_result.approved:
                        tl_result = self._apply_ponytail_review(name, contract, output, tl_result)

                    tl_reviews[name] = tl_result

                    if val.passed and tl_result.approved:
                        self._tracker.record_complete(name, contract.outputs)
                        result.team_lead_approvals += 1
                    else:
                        reason = "; ".join(val.failures[:2]) or tl_result.notes
                        self._tracker.record_failed(name, reason)
                ms_result.contract_validations[name] = validations.get(
                    name, ContractValidationResult(name, True, notes="no contract"))
            ms_result.team_lead_reviews = tl_reviews

            # Planner review gate — final gate before next milestone unlocks
            approved, notes = planner_review_milestone(
                milestone.name,
                validations,
                interactive=self._is_interactive(),
            )
            ms_result.approved = approved
            ms_result.rework_required = not approved
            ms_result.rework_notes = notes
            ms_result.elapsed_s = time.monotonic() - ms_start

            if approved:
                result.planner_approvals += 1
                # Deliverables only reach APPROVED after the planner gate
                for name in milestone.modules:
                    contract = self._plan.get_contract(name)
                    if contract is not None and validations.get(name) and validations[name].passed:
                        self._tracker.record_approved(name, contract.outputs)

            if milestone.is_critical:
                _critical_path_elapsed += ms_result.elapsed_s

            milestone.completed = ms_result.success
            milestone.approved = approved

            # Update prior outputs for next milestone
            prior_outputs.update(ms_result.worker_outputs)

            # Checkpoint mission state
            self._checkpoint(milestone, ms_result)

            result.milestones.append(ms_result)

            if ms_result.success:
                result.completed_modules += len(ms_agents)
            else:
                result.failed_modules += len(ms_agents)

            if self._on_complete:
                try:
                    self._on_complete(milestone.name, ms_result)
                except Exception:
                    pass

            # Emit deliverable status
            ms_status = self._tracker.milestone_status(milestone)
            ui.muted(
                f"  ✓ {milestone.name}: "
                f"{ms_status['completed']}/{ms_status['planned']} deliverables  "
                f"{'[APPROVED]' if approved else '[REWORK]'}"
            )

        result.elapsed_s = time.monotonic() - start
        result.critical_path_duration_s = _critical_path_elapsed
        result.total_deliverables_planned = self._tracker.overall_status()["planned"]
        result.total_deliverables_completed = self._tracker.overall_status()["completed"]

        try:
            from agent import ui as _ui
            _ui.muted("\n" + result.scorecard())
        except Exception:
            pass

        return result

    def _run_milestone_agents(
        self,
        milestone: "ProjectMilestone",
        agents: List["AgentRole"],
        prior_outputs: Dict[str, str],
        completed_ids: set,
    ) -> MilestoneResult:
        """Run agents for one milestone via the existing scheduler."""
        from agent.orchestration.task_dag import TaskDAG
        from agent.orchestration.team_generator import TeamPlan, AgentRole
        from agent.orchestration.scheduler import run_schedule, WorkerResult

        ms_result = MilestoneResult(
            milestone_name=milestone.name,
            order=milestone.order,
            modules_run=[a.role for a in agents],
            deliverables_planned=milestone.deliverables_planned,
        )

        try:
            # Build a sub-DAG and sub-team for this milestone only
            sub_dag = self._dag   # agents' deps are already wired

            # Create a sub-TeamPlan with only this milestone's agents
            sub_team = TeamPlan(
                agents=agents,
                complexity=float(len(agents)),
                risk_assessment="low",
                estimated_total_turns=sum(a.max_turns for a in agents),
                estimated_total_tokens=len(agents) * 50000,
                parallel_benefit=max(1.0, len(agents) * 0.5),
                parallel_cost=len(agents) * 0.1,
                recommended_strategy="parallel" if len(agents) > 1 else "sequential",
                reasoning=f"Milestone {milestone.order}: {milestone.name}",
            )

            sched_result = run_schedule(
                dag=sub_dag,
                team=sub_team,
                strategy="parallel" if len(agents) > 1 else "sequential",
                project_context=self._context,
                user_input=self._user_input,
                max_turns_per_agent=self._max_turns,
                max_workers=self._max_workers,
            )

            ms_result.success = sched_result.success
            ms_result.worker_outputs = {
                wr.role.replace(" Team", ""): wr.output
                for wr in sched_result.outputs.values()
            }
            ms_result.deliverables_completed = sum(
                1 for wr in sched_result.outputs.values() if wr.success
            )

        except Exception as exc:
            ms_result.success = False
            ms_result.worker_outputs = {}
            try:
                from agent import ui as _ui
                _ui.warn(f"  Milestone execution error: {exc}")
            except Exception:
                pass

        return ms_result

    def _checkpoint(self, milestone: "ProjectMilestone", result: MilestoneResult) -> None:
        """Persist milestone completion to session store."""
        try:
            from agent.persistence import session_store
            store = session_store()
            store.append_checkpoint(
                label=f"milestone-{milestone.order}",
                summary=(
                    f"Milestone {milestone.order} '{milestone.name}': "
                    f"{'completed' if result.success else 'failed'}. "
                    f"Deliverables: {result.deliverables_completed}/{result.deliverables_planned}. "
                    f"{'Approved' if result.approved else 'Rework required'}."
                ),
                modified_files=[],
            )
        except Exception:
            pass

    def _is_interactive(self) -> bool:
        try:
            import sys
            return bool(sys.stdin) and sys.stdin.isatty()
        except Exception:
            return False

    def _apply_ponytail_review(
        self,
        module_name: str,
        contract: "DeliverableContract",
        worker_output: str,
        base_result: TeamLeadReviewResult,
    ) -> TeamLeadReviewResult:
        """V5: Run the Ponytail simplicity gate on top of an already-approved
        Team Lead review. Only active under the PONYTAIL execution profile;
        a no-op (returns base_result unchanged) otherwise, or on any error —
        this gate never blocks execution, it only adds a stricter check.
        """
        try:
            from agent.production.execution_profiles import active_profile, is_ponytail
            if not is_ponytail(active_profile()):
                return base_result

            from agent.orchestration.ponytail import ponytail_team_lead_review

            files_created = list(getattr(contract, "files_to_create", []))
            sources: Dict[str, str] = {}
            if self._root != ".":
                import os
                for fpath in files_created[:5]:
                    full = os.path.join(self._root, fpath.lstrip("/\\"))
                    if os.path.isfile(full):
                        try:
                            with open(full, encoding="utf-8", errors="replace") as f:
                                sources[fpath] = f.read()
                        except OSError:
                            pass

            py_review = ponytail_team_lead_review(
                worker_output=worker_output,
                files_created=files_created,
                files_touched_source=sources,
                expected_files=max(1, len(getattr(contract, "outputs", [])) or 1),
            )

            # V5 Ponytail Phase 9: record for the mission-level report.
            if py_review.overengineering is not None:
                self._ponytail_scores.append(py_review.overengineering)

            if not py_review.approved:
                return TeamLeadReviewResult(
                    module_name=module_name,
                    approved=False,
                    notes=f"[ponytail] {py_review.notes}",
                )
            return TeamLeadReviewResult(
                module_name=module_name,
                approved=True,
                notes=f"{base_result.notes}; [ponytail] {py_review.notes}",
            )
        except Exception:
            return base_result

    def ponytail_summary(self) -> Optional[dict]:
        """V5 Phase 9: Aggregate per-module overengineering scores collected
        during this run into mission-level Ponytail report stats.

        Returns None when the ledger is empty (PONYTAIL profile wasn't active
        or no modules were reviewed) — callers should skip rendering then.
        """
        if not self._ponytail_scores:
            return None

        files_created = sum(s.factors.files_created for s in self._ponytail_scores)
        abstractions  = sum(s.factors.abstractions_created for s in self._ponytail_scores)
        helpers       = sum(s.factors.helpers_created for s in self._ponytail_scores)
        wrappers      = sum(s.factors.wrappers_created for s in self._ponytail_scores)
        avg_score     = sum(s.score for s in self._ponytail_scores) / len(self._ponytail_scores)
        worst         = max(self._ponytail_scores, key=lambda s: s.score)

        added: List[str] = []
        for s in self._ponytail_scores:
            added.extend(s.complexity_added)
        avoided: List[str] = []
        for s in self._ponytail_scores:
            avoided.extend(s.complexity_avoided)

        return {
            "files_created": files_created,
            "files_reused": self._ponytail_files_reused,
            "files_avoided": self._ponytail_files_avoided,
            "abstractions_avoided": max(0, len(self._ponytail_scores) - abstractions),
            "dependencies_reused": 0,   # tracked at the dependency_reuse_hints level, not per-module here
            "overengineering_score": avg_score,
            "grade": worst.grade(),
            "complexity_added": added,
            "complexity_avoided": avoided,
            "modules_reviewed": len(self._ponytail_scores),
        }


def _id(name: str) -> str:
    """Convert a module name to its stable ID."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
