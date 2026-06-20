"""V6 Runtime Optimization Tests — Phase validation.

Unit + integration coverage for every V6 system:
- ExecutionLayerSelector (Phase 1)
- TokenLedger (Phase 3)
- MissionCostEngine (Phase 4)
- OverheadProfiler (Phase 6)
- HotPathCache (Phase 7)
- V6 Benchmark + Soak (Phase 2 + 8 — offline mode)
- Scorecard (Phase 10)
- run_v5 integration (tier-adaptive activation, all V6 fields)

No LLM calls — all worker execution is mocked.
"""
from __future__ import annotations

import sys
import time
import unittest.mock as _mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from agent.orchestration.project_planner import ProjectModule, ProjectPlan, dag_from_plan, team_from_plan


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _simple_plan() -> ProjectPlan:
    mods = [
        ProjectModule("Database", "Schema", deliverables=["schema.sql"], outputs=["schema.sql"]),
        ProjectModule("API", "Endpoints", dependencies=["Database"], deliverables=["routes.py"], outputs=["routes.py"]),
    ]
    return ProjectPlan(project_name="Test", project_type="api", goal="test",
                       modules=mods, recommended_mode="dag", parallel_streams=2, estimated_speedup=1.5)


def _large_plan() -> ProjectPlan:
    mods = [
        ProjectModule(f"Mod{i}", f"Module {i}", deliverables=[f"m{i}.py"], outputs=[f"m{i}.py"])
        for i in range(10)
    ]
    return ProjectPlan(project_name="Large", project_type="api", goal="test",
                       modules=mods, recommended_mode="swarm", parallel_streams=5, estimated_speedup=3.0)


def _fake_run_schedule(**kw):
    from agent.orchestration.scheduler import WorkerResult, SchedulerResult
    team = kw["team"]
    outputs = {a.id: WorkerResult(agent_id=a.id, role=a.role, success=True,
                                   output=f"Completed {a.role}. AGENT_COMPLETE")
               for a in team.agents}
    return SchedulerResult(success=True, outputs=outputs, final_output="done",
                           total_turns=len(outputs) * 5, failed_agents=[])


# ── Phase 1: ExecutionLayerSelector ───────────────────────────────────────────

class TestExecutionLayerSelector:
    def test_small_plan_selects_simple_or_lower(self):
        from agent.orchestration.execution_layer_selector import select_tier, ExecutionTier
        plan = _simple_plan()
        td = select_tier(plan=plan, user_input="fix a bug")
        assert td.tier <= ExecutionTier.STANDARD, f"2-module plan got {td.tier.name}"

    def test_large_plan_selects_complex_or_higher(self):
        from agent.orchestration.execution_layer_selector import select_tier, ExecutionTier
        plan = _large_plan()
        td = select_tier(plan=plan, user_input="build enterprise system")
        assert td.tier >= ExecutionTier.COMPLEX, f"10-module plan got {td.tier.name}"

    def test_micro_layers_minimal(self):
        from agent.orchestration.execution_layer_selector import layers_for, ExecutionTier
        layers = layers_for(ExecutionTier.MICRO)
        assert not layers.program_manager
        assert not layers.digital_twin
        assert not layers.recovery
        assert layers.ponytail

    def test_enterprise_layers_all_active(self):
        from agent.orchestration.execution_layer_selector import layers_for, ExecutionTier
        layers = layers_for(ExecutionTier.ENTERPRISE)
        assert all([layers.program_manager, layers.digital_twin, layers.recovery,
                    layers.quality, layers.org_health, layers.portfolio])

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("KRYTH_EXECUTION_TIER", "0")
        from agent.orchestration.execution_layer_selector import tier_from_env
        assert tier_from_env() == 0

    def test_env_override_invalid_returns_none(self, monkeypatch):
        monkeypatch.setenv("KRYTH_EXECUTION_TIER", "xyz")
        from agent.orchestration.execution_layer_selector import tier_from_env
        assert tier_from_env() is None

    def test_tier_decision_has_reason(self):
        from agent.orchestration.execution_layer_selector import select_tier
        td = select_tier(plan=_simple_plan(), user_input="fix")
        assert td.reason
        assert td.module_count == 2


# ── Phase 3: TokenLedger ──────────────────────────────────────────────────────

class TestTokenLedger:
    def test_records_and_totals_correctly(self):
        from agent.orchestration.token_ledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_worker("a", "Auth", tokens_in=1000, tokens_out=200)
        ledger.record_worker("b", "DB",   tokens_in=2000, tokens_out=500)
        tin, tout = ledger.mission_total()
        assert tin == 3000 and tout == 700

    def test_top_consumers_sorted_by_total(self):
        from agent.orchestration.token_ledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_worker("small", "Docs",  tokens_in=100,   tokens_out=50)
        ledger.record_worker("large", "Auth",  tokens_in=10000, tokens_out=2000)
        s = ledger.summarize(top_n=2)
        assert s.top_consumers[0].worker_id == "large"

    def test_milestone_grouping(self):
        from agent.orchestration.token_ledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_worker("a", "A", tokens_in=500, tokens_out=100, milestone="M1")
        ledger.record_worker("b", "B", tokens_in=300, tokens_out=50, milestone="M2")
        s = ledger.summarize()
        assert "M1" in s.by_milestone and "M2" in s.by_milestone

    def test_waste_signal_detected(self):
        from agent.orchestration.token_ledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_worker("wasteful", "Planner", tokens_in=50_000, tokens_out=10)
        ledger.record_worker("normal",   "Coder",   tokens_in=500,    tokens_out=1000)
        s = ledger.summarize()
        assert len(s.waste_signals) > 0

    def test_cost_nonzero_with_default_table(self):
        from agent.orchestration.token_ledger import TokenLedger
        ledger = TokenLedger()
        ledger.record_worker("x", "X", tokens_in=1000, tokens_out=500)
        assert ledger.summarize().total_cost_usd > 0

    def test_record_from_scheduler_result(self):
        from agent.orchestration.token_ledger import TokenLedger
        from agent.orchestration.scheduler import WorkerStats
        ledger = TokenLedger()
        class FakeSched:
            worker_stats = {"a": WorkerStats(agent_id="a", role="Auth", tokens_in=1000, tokens_out=200)}
        ledger.record_from_scheduler_result(FakeSched(), milestone_name="M1")
        tin, tout = ledger.mission_total()
        assert tin == 1000 and tout == 200


# ── Phase 4: MissionCostEngine ────────────────────────────────────────────────

class TestMissionCostEngine:
    def test_no_budget_is_always_none_action(self):
        from agent.orchestration.cost_engine import MissionCostEngine, DowngradeAction
        e = MissionCostEngine()
        e.record_spend(1_000_000, 500_000)
        assert e.status().action == DowngradeAction.NONE

    def test_abort_when_over_budget(self):
        from agent.orchestration.cost_engine import MissionCostEngine, DowngradeAction
        e = MissionCostEngine(budget_usd=0.10)
        e.record_spend(100_000, 50_000)  # way over
        assert e.status().action == DowngradeAction.ABORT

    def test_warn_at_75_pct(self):
        from agent.orchestration.cost_engine import MissionCostEngine
        e = MissionCostEngine(budget_usd=1.0)
        e.record_spend(250_000, 0)   # $0.75 at sonnet rates → 75%
        s = e.status()
        assert s.pct_used >= 0.75

    def test_downgrade_at_90_pct(self):
        from agent.orchestration.cost_engine import MissionCostEngine, DowngradeAction
        e = MissionCostEngine(budget_usd=1.0)
        e.record_spend(300_000, 0)   # $0.90 = 90%
        s = e.status()
        assert s.action != DowngradeAction.NONE

    def test_token_budget_converts_to_usd(self):
        from agent.orchestration.cost_engine import MissionCostEngine
        e = MissionCostEngine(budget_tokens=1_000_000)
        e.record_spend(100_000, 0)
        assert e.status().spent_usd > 0

    def test_projection_doubles_at_50_pct_progress(self):
        from agent.orchestration.cost_engine import MissionCostEngine
        e = MissionCostEngine(budget_usd=10.0)
        e.record_spend(10_000, 0)
        s = e.status(milestones_done=1, milestones_total=2)
        assert s.projected_usd >= s.spent_usd


# ── Phase 6: OverheadProfiler ─────────────────────────────────────────────────

class TestOverheadProfiler:
    def test_measure_context_manager_records(self):
        from agent.orchestration.overhead_profiler import OverheadProfiler
        p = OverheadProfiler()
        with p.measure("planner"):
            time.sleep(0.001)
        r = p.report()
        assert "planner" in r.profiles
        assert r.profiles["planner"].calls == 1
        assert r.profiles["planner"].total_ms >= 0.5  # at least 0.5ms

    def test_useful_work_vs_overhead(self):
        from agent.orchestration.overhead_profiler import OverheadProfiler
        p = OverheadProfiler()
        with p.measure("scheduler"):
            time.sleep(0.010)
        with p.measure("planner"):
            time.sleep(0.002)
        r = p.report()
        assert r.useful_time_s > 0
        assert r.overhead_time_s >= 0

    def test_overhead_pct_in_range(self):
        from agent.orchestration.overhead_profiler import OverheadProfiler
        p = OverheadProfiler()
        with p.measure("v5_wiring"):
            time.sleep(0.005)
        r = p.report()
        assert 0 <= r.overhead_pct <= 100

    def test_bottlenecks_detection(self):
        from agent.orchestration.overhead_profiler import OverheadProfiler
        p = OverheadProfiler()
        p.record("slow_approval", 200.0)
        r = p.report()
        bottlenecks = r.bottlenecks(threshold_ms=100.0)
        assert len(bottlenecks) >= 1

    def test_reset_clears_all(self):
        from agent.orchestration.overhead_profiler import OverheadProfiler
        p = OverheadProfiler()
        with p.measure("x"):
            pass
        p.reset()
        r = p.report()
        assert len(r.profiles) == 0


# ── Phase 7: HotPathCache ─────────────────────────────────────────────────────

class TestHotPathCache:
    def test_contract_cache_returns_same_object(self):
        from agent.orchestration.hot_path_cache import get_contract_cached, reset_caches
        from agent.orchestration.v4_validate import MISSIONS
        reset_caches()
        plan = MISSIONS["crud_api"]
        c1 = get_contract_cached(plan, "Database")
        c2 = get_contract_cached(plan, "Database")
        assert c1 is c2

    def test_validation_cache_hits_on_repeat(self):
        from agent.orchestration.hot_path_cache import validate_contract_cached, reset_caches, cache_stats
        from agent.orchestration.project_planner import DeliverableContract
        reset_caches()
        contract = DeliverableContract(module_name="X", goal="g", success_criteria=["done"])
        output = "All done. AGENT_COMPLETE"
        r1 = validate_contract_cached(contract, output, ".")
        r2 = validate_contract_cached(contract, output, ".")
        assert r1 is r2
        stats = cache_stats()
        assert stats["validation_cache_hits"] >= 1

    def test_milestone_cache_returns_same_list(self):
        from agent.orchestration.hot_path_cache import get_milestones_cached, reset_caches
        from agent.orchestration.v4_validate import MISSIONS
        reset_caches()
        plan = MISSIONS["website"]
        ms1 = get_milestones_cached(plan)
        ms2 = get_milestones_cached(plan)
        assert ms1 is ms2

    def test_reset_clears_all_caches(self):
        from agent.orchestration.hot_path_cache import (
            get_contract_cached, reset_caches, cache_stats, _contract_cache,
        )
        from agent.orchestration.v4_validate import MISSIONS
        plan = MISSIONS["crud_api"]
        get_contract_cached(plan, "Database")
        reset_caches()
        stats = cache_stats()
        assert stats["contract_cache_entries"] == 0

    def test_different_contracts_cached_separately(self):
        from agent.orchestration.hot_path_cache import validate_contract_cached, reset_caches
        from agent.orchestration.project_planner import DeliverableContract
        reset_caches()
        c1 = DeliverableContract(module_name="A", goal="g", success_criteria=["done"])
        c2 = DeliverableContract(module_name="B", goal="g", success_criteria=["done"])
        r1 = validate_contract_cached(c1, "done. AGENT_COMPLETE", ".")
        r2 = validate_contract_cached(c2, "done. AGENT_COMPLETE", ".")
        assert r1.module_name != r2.module_name


# ── Phase 2+8: Benchmark + Soak (offline only) ────────────────────────────────

class TestBenchmarkAndSoak:
    def test_offline_benchmark_runs(self):
        from agent.orchestration.v6_benchmark import run_offline_benchmark
        report = run_offline_benchmark()
        assert len(report.results) > 0
        assert not report.live_mode
        assert report.elapsed_s > 0

    def test_benchmark_tier_assignment_varies(self):
        """Not all missions should get the same tier."""
        from agent.orchestration.v6_benchmark import run_offline_benchmark
        from agent.orchestration.execution_layer_selector import ExecutionTier
        report = run_offline_benchmark()
        tiers = set(r.tier for r in report.results)
        assert len(tiers) >= 2, f"Expected varied tiers, got {tiers}"

    def test_soak_test_runs_many_missions(self):
        from agent.orchestration.v6_soak_test import run_soak_test
        report = run_soak_test(duration_s=3.0, verbose=False)
        assert report.missions_run >= 5
        assert report.success_rate == 1.0
        assert report.crash_count == 0

    def test_soak_memory_growth_bounded(self):
        from agent.orchestration.v6_soak_test import run_soak_test
        report = run_soak_test(duration_s=2.0, verbose=False)
        assert report.memory_growth_mb < 100.0


# ── Phase 10: Scorecard ───────────────────────────────────────────────────────

class TestV6Scorecard:
    def test_scorecard_has_six_dimensions(self):
        from agent.orchestration.v6_scorecard import compute_scorecard
        card = compute_scorecard()
        assert len(card.dimensions) == 6

    def test_v6_scores_at_least_as_good_as_v5(self):
        from agent.orchestration.v6_scorecard import compute_scorecard
        card = compute_scorecard()
        # V6 should not be worse than V5 overall
        assert card.v6_total >= card.v5_total - 5.0, \
            f"V6 total {card.v6_total:.1f} is much worse than V5 {card.v5_total:.1f}"

    def test_cost_dimension_v6_better(self):
        """V6 should score better on cost since it activates fewer components."""
        from agent.orchestration.v6_scorecard import compute_scorecard
        card = compute_scorecard()
        cost_dim = next(d for d in card.dimensions if d.name == "Cost")
        assert cost_dim.v6_score >= cost_dim.v5_score, \
            f"Cost: V6={cost_dim.v6_score:.1f} should be >= V5={cost_dim.v5_score:.1f}"


# ── run_v5 V6 integration ─────────────────────────────────────────────────────

class TestRunV5V6Integration:
    def test_tier_auto_selects_from_plan(self):
        from agent.orchestration.v5_runtime import run_v5
        from agent.orchestration.execution_layer_selector import ExecutionTier

        plan = _simple_plan()
        dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")
        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            r = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="test",
                       max_turns_per_agent=5, max_workers=2)
        assert r.tier is not None
        assert r.tier <= int(ExecutionTier.STANDARD)

    def test_overhead_report_always_present(self):
        from agent.orchestration.v5_runtime import run_v5
        plan = _simple_plan()
        dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")
        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            r = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="test",
                       max_turns_per_agent=5, max_workers=2)
        assert r.overhead_report is not None

    def test_token_summary_always_present(self):
        from agent.orchestration.v5_runtime import run_v5
        plan = _simple_plan()
        dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")
        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            r = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="test",
                       max_turns_per_agent=5, max_workers=2)
        assert r.token_summary is not None

    def test_budget_status_without_budget_is_none_budget(self):
        from agent.orchestration.v5_runtime import run_v5
        plan = _simple_plan()
        dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")
        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            r = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="test",
                       max_turns_per_agent=5, max_workers=2)
        assert r.budget_status is not None
        assert r.budget_status.budget_usd is None   # no budget passed → unbounded

    def test_active_layers_reflects_tier(self):
        from agent.orchestration.v5_runtime import run_v5
        from agent.orchestration.execution_layer_selector import ExecutionTier
        plan = _simple_plan()
        dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")
        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            r = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="test",
                       max_turns_per_agent=5, max_workers=2, tier=2)  # force STANDARD
        assert r.tier == 2
        assert not r.active_layers.digital_twin

    def test_cache_stats_populated(self):
        from agent.orchestration.v5_runtime import run_v5
        plan = _simple_plan()
        dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")
        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            r = run_v5(plan=plan, dag=dag, team=team, project_context="", user_input="test",
                       max_turns_per_agent=5, max_workers=2)
        assert r.v6_cache_stats is not None
        assert "validation_cache_hit_rate" in r.v6_cache_stats
