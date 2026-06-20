"""V5Runtime — Execution Intelligence & Autonomous Operations integration layer.

Wraps a MilestoneEngine run with the V5 intelligence stack, using ONLY the
extension points MilestoneEngine already exposes (on_milestone_complete
callback, resume_from_order for recovery) — MilestoneEngine, the DAG, the
Planner, and the Scheduler are never modified.

Wires together (additive, all optional, all degrade gracefully on error):
  Phase 1  team_lead_runtime.ProgramManager   — independent review aggregation
  Phase 2  dynamic_replanner.DynamicReplanner  — minimal-scope replanning
  Phase 4  resource_scheduler.ResourceScheduler — advisory model/tier routing
  Phase 5  digital_twin.MissionDigitalTwin      — planned vs actual ground truth
  Phase 6  blocker_intelligence.BlockerIntelligence — blocker tracking
  Phase 7  org_health.OrgHealthTracker          — utilization / bottlenecks
  Phase 9  execution_forecaster.ExecutionForecaster — continuous ETA
  Phase 10 autonomous_recovery.AutonomousRecoveryEngine — intelligent retry
  Phase 11 quality_engine.compute_quality_report — mission quality score

Mission-level recovery loop (Phase 10): when MilestoneEngine.run() finishes
with a failed milestone, AutonomousRecoveryEngine decides an action; for any
non-ESCALATE action a NEW MilestoneEngine is created with
resume_from_order=<failed milestone's order> (V4's existing recovery
mechanism) and re-run. Bounded by MAX_ATTEMPTS_PER_TARGET so a broken
milestone escalates instead of looping forever.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class V5MissionReport:
    """Everything the V5/V6 intelligence stack observed during one mission."""
    mission_result: object                       # MissionDeliveryResult
    program_manager_report: object = None         # ProgramManagerReport
    digital_twin_snapshot: object = None           # TwinSnapshot
    blocker_report: object = None                  # BlockerReport
    org_health_report: object = None                # OrgHealthReport
    resource_plan: object = None                     # ResourcePlan
    forecast: object = None                           # ForecastSnapshot (final)
    quality_report: object = None                      # QualityReport
    recovery_attempts: int = 0
    recovery_summary: str = ""
    elapsed_s: float = 0.0
    # V6 fields — tier selection, real token accounting, overhead profiling
    tier: int = 4                                    # ExecutionTier (0-4)
    token_summary: object = None                     # TokenSummary (real tokens)
    overhead_report: object = None                   # OverheadReport
    budget_status: object = None                     # BudgetStatus
    active_layers: object = None                     # LayerSet
    v6_cache_stats: dict = None                      # hot_path cache hit rates


def _est_tokens_per_deliverable() -> int:
    return 3000   # consistent with the rough estimate used elsewhere (team_from_plan)


def run_v5(
    *,
    plan,                  # ProjectPlan
    dag,                   # TaskDAG
    team,                  # TeamPlan
    project_context: str = "",
    user_input: str = "",
    max_turns_per_agent: int = 80,
    max_workers: int = 4,
    project_root: str = ".",
    resume_from_order: int = 1,
    max_mission_retries: int = 3,
    tier: int = None,                # V6: ExecutionTier (None = auto-detect from plan)
    budget_usd: float = None,        # V6: mission cost budget (None = unbounded)
    budget_tokens: int = None,       # V6: token budget alternative to USD
) -> V5MissionReport:
    """Run a mission through MilestoneEngine with the V5/V6 intelligence stack.

    V6: tier-adaptive activation — only the layers needed for this mission's
    complexity are instantiated. A 2-module task runs STANDARD (no digital twin,
    no program manager, no portfolio) instead of always spinning up the full
    ENTERPRISE stack.

    Never raises — any component failure degrades to "that report is None".
    """
    from agent.orchestration.milestone_engine import MilestoneEngine, MissionDeliveryResult
    from agent.orchestration.team_lead_runtime import ProgramManager
    from agent.orchestration.dynamic_replanner import DynamicReplanner
    from agent.orchestration.resource_scheduler import ResourceScheduler
    from agent.orchestration.digital_twin import MissionDigitalTwin
    from agent.orchestration.blocker_intelligence import BlockerIntelligence
    from agent.orchestration.org_health import OrgHealthTracker
    from agent.orchestration.execution_forecaster import ExecutionForecaster
    from agent.orchestration.autonomous_recovery import AutonomousRecoveryEngine, RecoveryAction
    from agent.orchestration.quality_engine import compute_quality_report
    # V6 modules
    from agent.orchestration.execution_layer_selector import select_tier, layers_for, ExecutionTier, tier_from_env
    from agent.orchestration.token_ledger import TokenLedger
    from agent.orchestration.cost_engine import MissionCostEngine
    from agent.orchestration.overhead_profiler import OverheadProfiler
    from agent.orchestration.hot_path_cache import (
        get_contract_cached, get_milestones_cached, reset_caches, cache_stats,
    )

    t0 = time.monotonic()
    mission_name = getattr(plan, "project_name", "mission")

    # V6 Phase 1: resolve tier — env override → explicit caller → auto-detect
    _env_tier = tier_from_env()
    if _env_tier is not None:
        tier = _env_tier                       # env always wins
    elif tier is None:
        # Auto-detect from plan shape: no new LLM call
        try:
            _td = select_tier(plan=plan, user_input=user_input)
            tier = int(_td.tier)
        except Exception:
            tier = 4                           # safe fallback: full stack
    # else: caller passed an explicit tier — respect it unchanged
    _resolved_tier = tier if tier is not None else 4
    layers = layers_for(ExecutionTier(_resolved_tier))
    tier = _resolved_tier

    # V6 Phase 7: clear hot-path caches at mission start
    try:
        reset_caches()
    except Exception:
        pass

    # V6 Phase 6: overhead profiler (zero-cost when not read)
    profiler = OverheadProfiler()

    # V6 Phase 3: real token ledger
    token_ledger = TokenLedger()

    # V6 Phase 4: cost engine (no-budget by default; records even without budget)
    cost_engine = MissionCostEngine(budget_usd=budget_usd, budget_tokens=budget_tokens)

    # ── Set up the intelligence stack — gated by tier layers ────────────────
    program_mgr = ProgramManager(mission_name) if layers.program_manager else None
    twin = None
    if layers.digital_twin:
        twin = MissionDigitalTwin(plan)
        twin.set_project_root(project_root)
    blockers = BlockerIntelligence() if layers.blocker_intel else None
    org_health = OrgHealthTracker(mission_name) if layers.org_health else None

    # V6 Phase 7: use cached milestone structure (avoid repeated O(n) rebuild)
    with profiler.measure("milestone_build"):
        milestones_all = get_milestones_cached(plan)
    total_deliverables = sum(len(m.deliverables) for m in plan.modules)

    forecaster = None
    if layers.forecaster:
        forecaster = ExecutionForecaster(
            milestones_total=len(milestones_all),
            deliverables_total=total_deliverables,
        )

    resource_plan = None
    try:
        resource_plan = ResourceScheduler().allocate(team.agents)
    except Exception:
        pass

    recovery = AutonomousRecoveryEngine() if layers.recovery else None
    replanner = DynamicReplanner(plan, completed_milestones=set()) if layers.recovery else None

    # Running counters closed over by the milestone-complete callback.
    _state = {"ms_done": 0, "d_done": 0, "tokens": 0, "cp_elapsed": 0.0}

    def _bare(role: str) -> str:
        return role[:-5] if role.endswith(" Team") else role

    def _on_milestone_done(name: str, ms_result) -> None:
        # V6: record real tokens from WorkerStats before anything else
        try:
            token_ledger.record_from_scheduler_result(ms_result, milestone_name=name)
            tin, tout = token_ledger.mission_total()
            cost_engine.record_spend(tin, tout, note=f"after {name}")
        except Exception:
            pass

        if org_health is not None:
            try:
                org_health.ingest_milestone_result(ms_result)
            except Exception:
                pass

        if program_mgr is not None:
            try:
                # V6: use cached contract lookups (avoids repeated O(n) plan scan)
                bare_mods = [_bare(r) for r in ms_result.modules_run]
                contracts = {
                    mod: get_contract_cached(plan, mod)
                    for mod in bare_mods
                    if get_contract_cached(plan, mod) is not None
                }
                program_mgr.process_milestone(name, ms_result.worker_outputs, contracts, project_root)
            except Exception:
                pass

        if twin is not None:
            try:
                bare_mods_twin = [_bare(r) for r in ms_result.modules_run]
                for mod in bare_mods_twin:
                    contract = get_contract_cached(plan, mod)
                    if contract is not None and ms_result.success:
                        twin.record_deliverables(mod, list(contract.outputs))
                if ms_result.success:
                    twin.record_milestone_complete(name)
            except Exception:
                pass

        if blockers is not None:
            try:
                for mod, output in ms_result.worker_outputs.items():
                    blockers.infer_from_output(mod, mod, output, name)
            except Exception:
                pass

        if forecaster is not None:
            try:
                _state["ms_done"] += 1
                _state["d_done"] += ms_result.deliverables_completed
                # V6: use real token count from ledger if available, else fall back to proxy
                tin_real, _ = token_ledger.mission_total()
                _state["tokens"] = tin_real if tin_real > 0 else (
                    _state["d_done"] * _est_tokens_per_deliverable()
                )
                if ms_result.is_critical:
                    _state["cp_elapsed"] += ms_result.elapsed_s
                forecaster.update(
                    milestones_done=_state["ms_done"],
                    deliverables_done=_state["d_done"],
                    tokens_used=_state["tokens"],
                    critical_path_elapsed_s=_state["cp_elapsed"],
                )
            except Exception:
                pass

        if replanner is not None:
            try:
                if ms_result.success:
                    replanner.mark_completed(name)
            except Exception:
                pass

    # ── Initial run — profiled ─────────────────────────────────────────────
    engine = MilestoneEngine(
        plan=plan, dag=dag, team=team,
        project_context=project_context, user_input=user_input,
        max_turns_per_agent=max_turns_per_agent, max_workers=max_workers,
        project_root=project_root,
        on_milestone_complete=_on_milestone_done,
        resume_from_order=resume_from_order,
    )
    with profiler.measure("scheduler"):
        result = engine.run()

    # ── Phase 10: intelligent recovery loop (only when recovery layer active) ─
    attempts = 0
    while recovery is not None and not result.success and attempts < max_mission_retries:
        failed = [ms for ms in result.milestones if not ms.success]
        if not failed:
            break
        target = failed[0]

        decision = recovery.decide(
            error=target.rework_notes or "milestone execution failed",
            target=getattr(target, "milestone_name", str(attempts)),
        )
        if decision.action == RecoveryAction.ESCALATE:
            break

        retry_start = time.monotonic()
        retry_engine = MilestoneEngine(
            plan=plan, dag=dag, team=team,
            project_context=project_context, user_input=user_input,
            max_turns_per_agent=max_turns_per_agent, max_workers=max_workers,
            project_root=project_root,
            on_milestone_complete=_on_milestone_done,
            resume_from_order=target.order,
        )
        retry_result = retry_engine.run()
        retry_success = retry_result.success
        recovery.record_outcome(
            decision, success=retry_success, elapsed_s=time.monotonic() - retry_start,
        )
        result = _merge_results(result, retry_result)
        attempts += 1

    # ── Final reports ────────────────────────────────────────────────────────
    blocker_report = None
    try:
        if blockers is not None:
            blocker_report = blockers.generate_report()
    except Exception:
        pass

    org_health_report = None
    try:
        if org_health is not None:
            org_health_report = org_health.generate_report()
    except Exception:
        pass

    twin_snapshot = None
    try:
        if twin is not None:
            twin_snapshot = twin.snapshot()
    except Exception:
        pass

    pm_report = None
    try:
        if program_mgr is not None:
            pm_report = program_mgr.final_report()
    except Exception:
        pass

    quality_report = None
    try:
        if layers.quality:
            recovery_rate = recovery.success_rate() if recovery is not None else 1.0
            quality_report = compute_quality_report(mission_name, result, recovery_success_rate=recovery_rate)
    except Exception:
        pass

    # V6: finalize token ledger, overhead report, budget status, cache stats
    try:
        token_summary = token_ledger.summarize()
    except Exception:
        token_summary = None
    try:
        overhead_report = profiler.report()
    except Exception:
        overhead_report = None
    try:
        budget_status = cost_engine.status(
            milestones_done=_state["ms_done"],
            milestones_total=len(milestones_all),
        )
    except Exception:
        budget_status = None
    try:
        v6_cs = cache_stats()
    except Exception:
        v6_cs = None

    return V5MissionReport(
        mission_result=result,
        program_manager_report=pm_report,
        digital_twin_snapshot=twin_snapshot,
        blocker_report=blocker_report,
        org_health_report=org_health_report,
        resource_plan=resource_plan,
        forecast=forecaster.latest if forecaster is not None else None,
        quality_report=quality_report,
        recovery_attempts=attempts,
        recovery_summary=recovery.summary() if recovery is not None else "recovery layer not active",
        elapsed_s=time.monotonic() - t0,
        # V6
        tier=tier,
        token_summary=token_summary,
        overhead_report=overhead_report,
        budget_status=budget_status,
        active_layers=layers,
        v6_cache_stats=v6_cs,
    )


def _merge_results(prior, retry):
    """Combine two MissionDeliveryResult objects from a recovery retry —
    retry's milestones replace prior's at the same order, aggregates are
    recomputed from the combined milestone list."""
    from agent.orchestration.milestone_engine import MissionDeliveryResult

    retry_orders = {ms.order for ms in retry.milestones}
    combined = [ms for ms in prior.milestones if ms.order not in retry_orders] + list(retry.milestones)
    combined.sort(key=lambda ms: ms.order)

    merged = MissionDeliveryResult(
        project_name=prior.project_name,
        milestones=combined,
        total_modules=prior.total_modules,
        critical_path_names=prior.critical_path_names,
        recovered_from_checkpoint=True,
    )
    merged.completed_modules = sum(len(ms.modules_run) for ms in combined if ms.success)
    merged.failed_modules = sum(len(ms.modules_run) for ms in combined if not ms.success)
    merged.total_deliverables_planned = sum(ms.deliverables_planned for ms in combined)
    merged.total_deliverables_completed = sum(ms.deliverables_completed for ms in combined)
    merged.team_lead_approvals = sum(ms.team_lead_approved_count for ms in combined)
    merged.planner_approvals = sum(1 for ms in combined if ms.approved)
    merged.elapsed_s = prior.elapsed_s + retry.elapsed_s
    merged.critical_path_duration_s = prior.critical_path_duration_s + retry.critical_path_duration_s
    return merged
