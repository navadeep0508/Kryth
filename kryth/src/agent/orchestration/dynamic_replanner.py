"""DynamicReplanner — V5 Phase 2.

Triggers replanning when reality diverges from the plan:

  Triggers:
    dependency_failure   → regenerate affected milestone
    provider_failure     → switch provider + retry milestone
    milestone_failure    → regenerate milestone plan
    contract_failure     → rework specific module contract
    test_failure         → add test-fix milestone
    user_scope_change    → append new milestones
    deployment_failure   → add deployment-fix milestone

Rules:
  * Completed milestones are NEVER replanned (preserved)
  * Completed contracts are NEVER regenerated (preserved)
  * Session checkpoints are NEVER discarded
  * Only the minimum affected scope is regenerated

Additive: does not touch MilestoneEngine, Scheduler, or Planner internals.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class ReplanTrigger(Enum):
    DEPENDENCY_FAILURE  = "dependency_failure"
    PROVIDER_FAILURE    = "provider_failure"
    MILESTONE_FAILURE   = "milestone_failure"
    CONTRACT_FAILURE    = "contract_failure"
    TEST_FAILURE        = "test_failure"
    USER_SCOPE_CHANGE   = "user_scope_change"
    DEPLOYMENT_FAILURE  = "deployment_failure"


@dataclass
class ReplanDecision:
    trigger: ReplanTrigger
    affected_milestones: List[str]        # milestone names to regenerate
    preserved_milestones: List[str]       # milestone names to keep as-is
    action: str                           # human-readable description
    new_modules: List[str] = field(default_factory=list)
    provider_override: Optional[str] = None
    notes: str = ""
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class ReplanResult:
    decision: ReplanDecision
    success: bool
    updated_plan: Optional[object] = None   # ProjectPlan | None
    notes: str = ""
    elapsed_s: float = 0.0


# ── Trigger classification ────────────────────────────────────────────────────

def classify_trigger(
    error: str,
    context: str = "",
) -> ReplanTrigger:
    """Classify a failure string into a replan trigger."""
    low = (error + " " + context).lower()

    if any(k in low for k in ("provider", "api_error", "rate limit", "413", "timeout", "503")):
        return ReplanTrigger.PROVIDER_FAILURE
    if any(k in low for k in ("dependency", "blocked by", "depends on", "missing dep")):
        return ReplanTrigger.DEPENDENCY_FAILURE
    if any(k in low for k in ("test failed", "pytest", "assertion", "test_")):
        return ReplanTrigger.TEST_FAILURE
    if any(k in low for k in ("deploy", "dockerfile", "container", "kubernetes", "k8s")):
        return ReplanTrigger.DEPLOYMENT_FAILURE
    if any(k in low for k in ("contract", "deliverable", "output missing", "success criterion")):
        return ReplanTrigger.CONTRACT_FAILURE
    if any(k in low for k in ("scope change", "user request", "add feature", "new requirement")):
        return ReplanTrigger.USER_SCOPE_CHANGE
    return ReplanTrigger.MILESTONE_FAILURE


# ── Replanning decisions ──────────────────────────────────────────────────────

def decide_replan(
    trigger: ReplanTrigger,
    failed_milestone: Optional[str],
    completed_milestones: Set[str],
    all_milestones: List[str],
    failed_module: Optional[str] = None,
    new_scope: Optional[str] = None,
    available_providers: Optional[List[str]] = None,
) -> ReplanDecision:
    """Determine the minimum replanning action for a given trigger.

    Never touches completed milestones. Always preserves the maximum amount
    of already-done work.
    """
    preserved = list(completed_milestones)
    affected: List[str] = []
    action = ""
    new_modules: List[str] = []
    provider_override: Optional[str] = None

    if trigger == ReplanTrigger.PROVIDER_FAILURE:
        # Switch provider; retry only the failed milestone
        if failed_milestone and failed_milestone not in completed_milestones:
            affected = [failed_milestone]
        # Pick fallback provider
        if available_providers:
            provider_override = next(
                (p for p in available_providers if p != "primary"), None
            )
        action = (
            f"Provider failure — switching to {provider_override or 'fallback'}, "
            f"retrying {failed_milestone or 'current milestone'}"
        )

    elif trigger == ReplanTrigger.DEPENDENCY_FAILURE:
        # Find the failed milestone and all downstream milestones not yet done
        if failed_milestone:
            idx = next(
                (i for i, m in enumerate(all_milestones) if m == failed_milestone), -1
            )
            affected = [
                m for m in all_milestones[max(idx, 0):]
                if m not in completed_milestones
            ]
        action = f"Dependency failure in {failed_milestone} — replanning downstream milestones"

    elif trigger == ReplanTrigger.CONTRACT_FAILURE:
        # Only replan the specific module's milestone, not the whole mission
        if failed_milestone and failed_milestone not in completed_milestones:
            affected = [failed_milestone]
        action = (
            f"Contract failure ({failed_module or 'module'}) — "
            f"rework contract in {failed_milestone}"
        )

    elif trigger == ReplanTrigger.TEST_FAILURE:
        # Add a new test-fix pass after the current milestone
        if failed_milestone and failed_milestone not in completed_milestones:
            affected = [failed_milestone]
        new_modules = [f"Test Fix — {failed_module or failed_milestone}"]
        action = "Test failure — adding test-fix module to milestone"

    elif trigger == ReplanTrigger.DEPLOYMENT_FAILURE:
        # Add a deployment-fix milestone at the end
        new_modules = ["Deployment Fix"]
        action = "Deployment failure — appending deployment-fix milestone"

    elif trigger == ReplanTrigger.USER_SCOPE_CHANGE:
        # Append new milestones for the new scope; never touch completed
        if new_scope:
            new_modules = [f"Scope Extension — {new_scope[:40]}"]
        action = f"Scope change — appending new milestone(s)"

    else:  # MILESTONE_FAILURE
        if failed_milestone and failed_milestone not in completed_milestones:
            affected = [failed_milestone]
        action = f"Milestone failure — regenerating {failed_milestone}"

    return ReplanDecision(
        trigger=trigger,
        affected_milestones=affected,
        preserved_milestones=preserved,
        action=action,
        new_modules=new_modules,
        provider_override=provider_override,
    )


# ── Replanner ─────────────────────────────────────────────────────────────────

class DynamicReplanner:
    """Applies replanning decisions to a ProjectPlan without LLM calls.

    For provider/contract failures it patches the plan in-place (fast, no LLM).
    For scope changes it appends new ProjectModule stubs (placeholders for the
    next Planner LLM call when one is available).

    All previously completed milestones and their contracts are untouched.
    """

    def __init__(
        self,
        plan,                          # ProjectPlan
        completed_milestones: Optional[Set[str]] = None,
    ) -> None:
        self._plan = plan
        self._completed = set(completed_milestones or [])
        self._history: List[ReplanDecision] = []

    def apply(
        self,
        error: str,
        failed_milestone: Optional[str] = None,
        failed_module: Optional[str] = None,
        new_scope: Optional[str] = None,
        context: str = "",
        available_providers: Optional[List[str]] = None,
    ) -> ReplanResult:
        """Classify the error, decide the replan, apply to the plan."""
        t0 = time.monotonic()
        trigger = classify_trigger(error, context)
        all_ms_names = [
            ms.name for ms in (self._plan.structured_milestones or [])
        ] or [ms.name for ms in self._plan.ensure_structured_milestones()]

        decision = decide_replan(
            trigger=trigger,
            failed_milestone=failed_milestone,
            completed_milestones=self._completed,
            all_milestones=all_ms_names,
            failed_module=failed_module,
            new_scope=new_scope,
            available_providers=available_providers,
        )
        self._history.append(decision)

        try:
            updated_plan = self._patch_plan(decision)
            notes = decision.action
            success = True
        except Exception as exc:
            updated_plan = self._plan
            notes = f"Replan failed: {exc}"
            success = False

        return ReplanResult(
            decision=decision,
            success=success,
            updated_plan=updated_plan,
            notes=notes,
            elapsed_s=time.monotonic() - t0,
        )

    def _patch_plan(self, decision: ReplanDecision) -> object:
        """Mutate the plan minimally based on the decision. Returns the plan."""
        from agent.orchestration.project_planner import ProjectModule, ProjectMilestone

        plan = self._plan

        # Reset failed milestones so they re-execute
        for ms in plan.structured_milestones:
            if ms.name in decision.affected_milestones:
                ms.completed = False
                ms.approved  = False

        # Append new modules / milestones for scope extension or test/deploy fixes
        if decision.new_modules:
            next_order = max((ms.order for ms in plan.structured_milestones), default=0) + 1
            for mod_name in decision.new_modules:
                # Add the module
                plan.modules.append(ProjectModule(
                    name=mod_name,
                    goal=f"Auto-generated: {mod_name}",
                    deliverables=[f"{mod_name.lower().replace(' ', '_')}_done"],
                    estimated_turns=15,
                ))
                # Add a new milestone for it
                plan.structured_milestones.append(ProjectMilestone(
                    name=f"Milestone {next_order} — {mod_name}",
                    order=next_order,
                    modules=[mod_name],
                    goal=mod_name,
                    deliverables_planned=1,
                ))
                next_order += 1

        return plan

    def mark_completed(self, milestone_name: str) -> None:
        """Record a completed milestone so it is never affected by replanning."""
        self._completed.add(milestone_name)

    @property
    def history(self) -> List[ReplanDecision]:
        return list(self._history)

    @property
    def completed_milestones(self) -> Set[str]:
        return set(self._completed)
