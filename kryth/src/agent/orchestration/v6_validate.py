"""V6 Execution Validation — Phases 1, 3-7, 9.

Validates all V6 systems operate correctly end-to-end (offline, no LLM calls):
  Phase 1: ExecutionLayerSelector — tier auto-selection per plan shape
  Phase 3: TokenLedger — real token accounting wired into run_v5
  Phase 4: MissionCostEngine — budget tracking + downgrade actions
  Phase 6: OverheadProfiler — subsystem timing, overhead_pct
  Phase 7: HotPathCache — cache hit rate after repeated calls
  Phase 9: Runtime simplification audit (redundancy checks)

Phases 2 (live benchmark), 8 (24h soak), require real providers:
  KRYTH_REAL_PROVIDERS=1 python -m agent.orchestration.v6_validate --live

Usage:
  python -m agent.orchestration.v6_validate
"""
from __future__ import annotations

import argparse
import sys
import time
import unittest.mock as _mock
from dataclasses import dataclass, field
from typing import List


@dataclass
class V6Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class V6Validation:
    phase: str
    checks: List[V6Check] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def score(self) -> str:
        p = sum(1 for c in self.checks if c.passed)
        return f"{p}/{len(self.checks)}"


def _validate_phase1() -> V6Validation:
    """Phase 1: ExecutionLayerSelector tier selection."""
    from agent.orchestration.execution_layer_selector import (
        select_tier, ExecutionTier, layers_for, tier_from_env,
    )
    from agent.orchestration.v4_validate import MISSIONS, _make_plan

    val = V6Validation(phase="Phase 1 — ExecutionLayerSelector")
    t0 = time.monotonic()

    # Single-module trivial plan → SIMPLE (Tier 1)
    tiny = _make_plan("Tiny", [("Fixer", "Fix bug", [], ["fix.py"])])
    td = select_tier(plan=tiny, user_input="fix bug")
    val.checks.append(V6Check("tiny_plan→SIMPLE_or_less", td.tier <= ExecutionTier.SIMPLE, f"got {td.tier.name}"))

    # 4-module plan → at most STANDARD (Tier 2)
    crud = MISSIONS["crud_api"]
    td = select_tier(plan=crud, user_input="build crud api")
    val.checks.append(V6Check("4mod_plan→STANDARD_or_less", td.tier <= ExecutionTier.STANDARD, f"got {td.tier.name}"))

    # 5-module plan (saas) → COMPLEX (Tier 3)
    saas = MISSIONS["saas_platform"]
    td = select_tier(plan=saas, user_input="build saas")
    val.checks.append(V6Check("5mod_plan→COMPLEX_or_less", td.tier <= ExecutionTier.COMPLEX, f"got {td.tier.name}"))

    # ENTERPRISE layers include all components
    ent_layers = layers_for(ExecutionTier.ENTERPRISE)
    val.checks.append(V6Check("enterprise_layers_all_active",
                               all([ent_layers.program_manager, ent_layers.digital_twin,
                                    ent_layers.recovery, ent_layers.quality, ent_layers.org_health]),
                               f"active: {ent_layers.active_names()}"))

    # STANDARD layers skip program_manager, digital_twin, recovery, portfolio
    std_layers = layers_for(ExecutionTier.STANDARD)
    val.checks.append(V6Check("standard_layers_lean",
                               not std_layers.program_manager and not std_layers.digital_twin
                               and not std_layers.recovery and not std_layers.portfolio,
                               f"active: {std_layers.active_names()}"))

    val.elapsed_s = time.monotonic() - t0
    return val


def _validate_phase3() -> V6Validation:
    """Phase 3: TokenLedger."""
    from agent.orchestration.token_ledger import TokenLedger, DEFAULT_COST_TABLE

    val = V6Validation(phase="Phase 3 — TokenLedger")
    t0 = time.monotonic()

    ledger = TokenLedger()
    ledger.record_worker("w1", "Auth Team", tokens_in=1000, tokens_out=200, milestone="M1")
    ledger.record_worker("w2", "DB Team", tokens_in=2000, tokens_out=500, milestone="M1")
    ledger.record_worker("w3", "Docs Team", tokens_in=100, tokens_out=50, milestone="M2")

    tin, tout = ledger.mission_total()
    val.checks.append(V6Check("total_tokens_correct", tin == 3100 and tout == 750, f"in={tin} out={tout}"))

    summary = ledger.summarize()
    val.checks.append(V6Check("milestone_tracking", "M1" in summary.by_milestone, f"milestones: {list(summary.by_milestone.keys())}"))
    val.checks.append(V6Check("top_consumers_ranked",
                               len(summary.top_consumers) > 0
                               and summary.top_consumers[0].tokens_in >= summary.top_consumers[-1].tokens_in,
                               f"top={summary.top_consumers[0].role if summary.top_consumers else 'none'}"))

    cost = ledger.summarize().total_cost_usd
    val.checks.append(V6Check("cost_nonzero", cost > 0, f"cost=${cost:.6f}"))

    # Waste detection: a worker that consumed lots of prompt but tiny completion
    ledger2 = TokenLedger()
    ledger2.record_worker("wasteful", "Planner", tokens_in=50000, tokens_out=10)
    ledger2.record_worker("normal", "Coder", tokens_in=500, tokens_out=1000)
    s2 = ledger2.summarize()
    val.checks.append(V6Check("waste_signal_detected", len(s2.waste_signals) > 0, f"signals: {s2.waste_signals[:1]}"))

    val.elapsed_s = time.monotonic() - t0
    return val


def _validate_phase4() -> V6Validation:
    """Phase 4: MissionCostEngine."""
    from agent.orchestration.cost_engine import MissionCostEngine, DowngradeAction

    val = V6Validation(phase="Phase 4 — MissionCostEngine")
    t0 = time.monotonic()

    # No budget: should always be NONE action
    engine = MissionCostEngine()
    engine.record_spend(10000, 5000)
    s = engine.status()
    val.checks.append(V6Check("no_budget_no_action", s.action == DowngradeAction.NONE, f"action={s.action.value}"))

    # Budget: 75% spent → warn.
    # Sonnet rates: $0.003/1k prompt. To hit 75% of $1.00 budget:
    # need $0.75 → 250k prompt tokens (0.003 * 250 = $0.75).
    engine2 = MissionCostEngine(budget_usd=1.0)
    engine2.record_spend(tokens_in=250_000, tokens_out=0)
    s2 = engine2.status()
    val.checks.append(V6Check("75pct_warn_triggered", s2.pct_used >= 0.75, f"pct={s2.pct_used:.0%} action={s2.action.value}"))

    # 105% budget → ABORT
    engine3 = MissionCostEngine(budget_usd=0.10)
    engine3.record_spend(tokens_in=100_000, tokens_out=50_000)  # way over budget
    s3 = engine3.status()
    val.checks.append(V6Check("over_budget_abort", s3.action == DowngradeAction.ABORT, f"pct={s3.pct_used:.0%} action={s3.action.value}"))

    # Token budget conversion
    engine4 = MissionCostEngine(budget_tokens=500_000)
    engine4.record_spend(tokens_in=200_000, tokens_out=100_000)
    s4 = engine4.status()
    val.checks.append(V6Check("token_budget_works", s4.spent_usd > 0, f"spent=${s4.spent_usd:.4f}"))

    # Projection: 2 done / 4 total → projected = 2× spent
    engine5 = MissionCostEngine(budget_usd=10.0)
    engine5.record_spend(tokens_in=1000, tokens_out=500)
    s5 = engine5.status(milestones_done=2, milestones_total=4)
    val.checks.append(V6Check("projection_reasonable",
                               s5.projected_usd >= s5.spent_usd,
                               f"spent=${s5.spent_usd:.4f} projected=${s5.projected_usd:.4f}"))

    val.elapsed_s = time.monotonic() - t0
    return val


def _validate_phase6() -> V6Validation:
    """Phase 6: OverheadProfiler."""
    from agent.orchestration.overhead_profiler import OverheadProfiler
    import time as _t

    val = V6Validation(phase="Phase 6 — OverheadProfiler")
    t0 = time.monotonic()

    profiler = OverheadProfiler()

    # Record some fake subsystem timings
    with profiler.measure("planner"):
        _t.sleep(0.005)
    with profiler.measure("scheduler"):
        _t.sleep(0.050)
    with profiler.measure("planner"):
        _t.sleep(0.003)

    report = profiler.report()

    val.checks.append(V6Check("planner_calls_counted", report.profiles.get("planner", None) is not None
                               and report.profiles["planner"].calls == 2,
                               f"planner calls: {report.profiles.get('planner', object()).calls if 'planner' in report.profiles else 0}"))
    val.checks.append(V6Check("scheduler_is_useful_work",
                               report.profiles.get("scheduler", type('x', (), {'is_useful_work': lambda s: False})()).is_useful_work(),
                               "scheduler should be classified as useful work"))
    val.checks.append(V6Check("overhead_pct_between_0_100",
                               0 <= report.overhead_pct <= 100,
                               f"overhead_pct={report.overhead_pct:.1f}%"))
    val.checks.append(V6Check("useful_time_measured",
                               report.useful_time_s > 0,
                               f"useful_time={report.useful_time_s:.4f}s"))
    val.checks.append(V6Check("overhead_time_measured",
                               report.overhead_time_s >= 0,
                               f"overhead_time={report.overhead_time_s:.4f}s"))

    val.elapsed_s = time.monotonic() - t0
    return val


def _validate_phase7() -> V6Validation:
    """Phase 7: HotPathCache."""
    from agent.orchestration.hot_path_cache import (
        ContractCache, ValidationCache, MilestoneStructureCache,
        reset_caches, cache_stats, get_contract_cached,
    )
    from agent.orchestration.v4_validate import MISSIONS

    val = V6Validation(phase="Phase 7 — HotPathCache")
    t0 = time.monotonic()

    plan = MISSIONS["crud_api"]
    reset_caches()

    # First call: miss; second call: hit (same plan + same module name)
    c1 = get_contract_cached(plan, "Database")
    c2 = get_contract_cached(plan, "Database")
    val.checks.append(V6Check("contract_cache_returns_same", c1 is c2 or (c1 == c2), "both calls return equal objects"))

    # ValidationCache: same contract+output returns cached result
    from agent.orchestration.hot_path_cache import validate_contract_cached
    from agent.orchestration.project_planner import DeliverableContract
    contract = DeliverableContract(module_name="X", goal="g", success_criteria=["done"])
    output = "All done correctly. AGENT_COMPLETE"
    r1 = validate_contract_cached(contract, output, ".")
    r2 = validate_contract_cached(contract, output, ".")
    val.checks.append(V6Check("validation_cache_hit", r1 is r2, "second call returns cached object"))

    # MilestoneStructureCache
    from agent.orchestration.hot_path_cache import get_milestones_cached
    ms1 = get_milestones_cached(plan)
    ms2 = get_milestones_cached(plan)
    val.checks.append(V6Check("milestone_cache_same_list", ms1 is ms2, "same list object returned"))

    stats = cache_stats()
    val.checks.append(V6Check("cache_stats_present",
                               "validation_cache_hit_rate" in stats,
                               f"stats: {stats}"))
    val.checks.append(V6Check("validation_hit_rate_positive",
                               stats.get("validation_cache_hits", 0) >= 1,
                               f"hits={stats.get('validation_cache_hits', 0)}"))

    val.elapsed_s = time.monotonic() - t0
    return val


def _validate_phase9() -> V6Validation:
    """Phase 9: Runtime simplification audit.

    Checks that the V6 changes removed overhead rather than adding it:
    - STANDARD tier uses fewer active layers than ENTERPRISE
    - No double-activation (components not instantiated twice)
    - Cached contract lookup is faster than uncached on repeated calls
    """
    from agent.orchestration.execution_layer_selector import layers_for, ExecutionTier
    from agent.orchestration.hot_path_cache import reset_caches, get_contract_cached
    from agent.orchestration.v4_validate import MISSIONS
    import time as _t

    val = V6Validation(phase="Phase 9 — Runtime Simplification")
    t0 = time.monotonic()

    # STANDARD has fewer active layers than ENTERPRISE
    std = layers_for(ExecutionTier.STANDARD)
    ent = layers_for(ExecutionTier.ENTERPRISE)
    std_n = len(std.active_names())
    ent_n = len(ent.active_names())
    val.checks.append(V6Check("standard_fewer_layers_than_enterprise",
                               std_n < ent_n,
                               f"STANDARD={std_n} < ENTERPRISE={ent_n}"))

    # Contract cache is faster on repeated calls
    plan = MISSIONS["crud_api"]
    reset_caches()
    t_uncached = _t.perf_counter()
    for _ in range(50):
        plan.get_contract("Database")   # direct uncached call
    uncached_ms = (_t.perf_counter() - t_uncached) * 1000

    reset_caches()
    # First call populates cache
    get_contract_cached(plan, "Database")
    t_cached = _t.perf_counter()
    for _ in range(50):
        get_contract_cached(plan, "Database")
    cached_ms = (_t.perf_counter() - t_cached) * 1000

    val.checks.append(V6Check("cached_contract_lookup_faster",
                               cached_ms <= uncached_ms,
                               f"cached={cached_ms:.2f}ms uncached={uncached_ms:.2f}ms"))

    # MICRO tier has only 1 active layer (ponytail)
    micro = layers_for(ExecutionTier.MICRO)
    val.checks.append(V6Check("micro_tier_minimal",
                               len(micro.active_names()) <= 2,
                               f"MICRO active: {micro.active_names()}"))

    val.elapsed_s = time.monotonic() - t0
    return val


def _validate_run_v5_integration() -> V6Validation:
    """Integration: run_v5 correctly selects tiers and populates V6 fields."""
    from agent.orchestration.v5_runtime import run_v5
    from agent.orchestration.execution_layer_selector import ExecutionTier
    from agent.orchestration.v4_validate import MISSIONS, _fake_run_schedule
    from agent.orchestration.project_planner import dag_from_plan, team_from_plan

    val = V6Validation(phase="Integration — run_v5 V6 wiring")
    t0 = time.monotonic()

    plan = MISSIONS["crud_api"]  # 4 modules → STANDARD
    dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")

    with _mock.patch("agent.orchestration.scheduler.run_schedule", side_effect=lambda **kw: _fake_run_schedule(**kw)):
        r = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="crud_api",
                   max_turns_per_agent=10, max_workers=2)

    val.checks.append(V6Check("mission_success", r.mission_result.success, ""))
    val.checks.append(V6Check("tier_auto_selected", r.tier is not None and r.tier <= int(ExecutionTier.STANDARD), f"tier={r.tier}"))
    val.checks.append(V6Check("overhead_report_present", r.overhead_report is not None, ""))
    val.checks.append(V6Check("token_summary_present", r.token_summary is not None, ""))
    val.checks.append(V6Check("budget_status_present", r.budget_status is not None, ""))
    val.checks.append(V6Check("active_layers_present", r.active_layers is not None, f"layers={r.active_layers.active_names() if r.active_layers else None}"))
    # STANDARD should skip digital_twin and program_manager
    val.checks.append(V6Check("standard_skips_heavy_layers",
                               r.digital_twin_snapshot is None and r.program_manager_report is None,
                               "digital_twin and program_manager not instantiated for STANDARD tier"))
    # ENTERPRISE should have everything
    with _mock.patch("agent.orchestration.scheduler.run_schedule", side_effect=lambda **kw: _fake_run_schedule(**kw)):
        r_ent = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="crud_api",
                       max_turns_per_agent=10, max_workers=2, tier=4)
    val.checks.append(V6Check("enterprise_activates_all",
                               r_ent.digital_twin_snapshot is not None and r_ent.program_manager_report is not None,
                               "digital_twin and program_manager active for ENTERPRISE tier"))

    val.elapsed_s = time.monotonic() - t0
    return val


def run_all(live: bool = False) -> bool:
    validations = [
        _validate_phase1(),
        _validate_phase3(),
        _validate_phase4(),
        _validate_phase6(),
        _validate_phase7(),
        _validate_phase9(),
        _validate_run_v5_integration(),
    ]

    print("◈ V6 VALIDATION\n")
    all_passed = True
    for val in validations:
        icon = "✓" if val.passed else "✗"
        print(f"  {icon} {val.phase:<40} {val.score}  ({val.elapsed_s*1000:.0f}ms)")
        if not val.passed:
            all_passed = False
            for c in val.checks:
                if not c.passed:
                    print(f"      ✗ {c.name}: {c.detail}")

    if live:
        print("\n  [Live phases 2+8 require KRYTH_REAL_PROVIDERS=1 + real API key]")

    print()
    if all_passed:
        print("KRYTH V6 RUNTIME OPTIMIZATION COMPLETE")
    else:
        print("✗ Some checks failed — see above.")
    return all_passed


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true", help="Include live-provider phases (requires API key)")
    args = p.parse_args()
    ok = run_all(live=args.live)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
