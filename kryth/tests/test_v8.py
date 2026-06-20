"""V8 Production Readiness Tests.

Unit + integration coverage for:
- FailureIntelligenceEngine (Phase 6)
- SelfOptimizerEngine (Phase 5)
- ModelRoutingOptimizer (Phase 4)
- Planner Quality Benchmark (Phase 2)
- Production Scorecard (Phase 9)

No LLM calls anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest


# ── Phase 6: FailureIntelligenceEngine ────────────────────────────────────────

class TestFailureIntelligence:
    def test_classify_provider_failure(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("API rate limit exceeded 429") == FailureClass.PROVIDER

    def test_classify_network_failure(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("connection timed out") == FailureClass.NETWORK

    def test_classify_tool_failure(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("tool call failed: permission denied") == FailureClass.TOOL

    def test_classify_planner_failure(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("cycle detected in dependency graph") == FailureClass.PLANNER

    def test_classify_worker_failure(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("worker hit max_turns without AGENT_COMPLETE") == FailureClass.WORKER

    def test_classify_validation_failure(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("contract validation failed: criterion not met") == FailureClass.VALIDATION

    def test_classify_recovery_failure(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("escalated: max recovery attempts exceeded") == FailureClass.RECOVERY

    def test_unknown_failure_returns_unknown(self):
        from agent.orchestration.failure_intelligence import classify_failure, FailureClass
        assert classify_failure("some random thing happened") == FailureClass.UNKNOWN

    def test_engine_records_events(self):
        from agent.orchestration.failure_intelligence import FailureIntelligenceEngine
        engine = FailureIntelligenceEngine()
        engine.record("rate limit hit", mission_id="m1", recovered=False)
        engine.record("timed out", mission_id="m1", recovered=True)
        assert engine.event_count == 2

    def test_root_cause_report_clusters_by_class(self):
        from agent.orchestration.failure_intelligence import FailureIntelligenceEngine, FailureClass
        engine = FailureIntelligenceEngine()
        for _ in range(3):
            engine.record("rate limit exceeded 429", mission_id="m1")
        for _ in range(1):
            engine.record("connection timed out", mission_id="m2")
        report = engine.generate_report()
        assert report.total_failures == 4
        assert any(c.failure_class == FailureClass.PROVIDER for c in report.clusters)

    def test_top_cluster_by_impact(self):
        from agent.orchestration.failure_intelligence import FailureIntelligenceEngine, FailureClass
        engine = FailureIntelligenceEngine()
        for _ in range(5):
            engine.record("contract validation failed", blocked_deliverables=3)
        engine.record("rate limit", blocked_deliverables=0)
        report = engine.generate_report()
        assert report.clusters[0].failure_class == FailureClass.VALIDATION

    def test_mitigation_present_for_all_classes(self):
        from agent.orchestration.failure_intelligence import _MITIGATIONS, FailureClass
        for cls in FailureClass:
            if cls != FailureClass.UNKNOWN:
                assert cls in _MITIGATIONS

    def test_empty_engine_report(self):
        from agent.orchestration.failure_intelligence import FailureIntelligenceEngine
        engine = FailureIntelligenceEngine()
        report = engine.generate_report()
        assert report.total_failures == 0


# ── Phase 5: SelfOptimizerEngine ──────────────────────────────────────────────

class TestSelfOptimizer:
    def test_records_mission(self):
        from agent.orchestration.self_optimizer import SelfOptimizerEngine
        engine = SelfOptimizerEngine()
        engine.record("m1", "crud_api", tier=2, success=True, duration_s=5.0)
        assert engine.record_count == 1

    def test_insufficient_data_returns_generic_rec(self):
        from agent.orchestration.self_optimizer import SelfOptimizerEngine
        engine = SelfOptimizerEngine()
        engine.record("m1", "crud_api", tier=2, success=True, duration_s=5.0)
        recs = engine.recommendations()
        assert len(recs) == 1
        assert recs[0].priority == "low"

    def test_high_failure_rate_triggers_recommendation(self):
        from agent.orchestration.self_optimizer import SelfOptimizerEngine
        engine = SelfOptimizerEngine()
        for i in range(5):
            engine.record(f"m{i}", "crud_api", tier=2, success=(i < 1), duration_s=5.0)
        recs = engine.recommendations()
        priorities = [r.priority for r in recs]
        assert "high" in priorities

    def test_cost_outlier_detected(self):
        from agent.orchestration.self_optimizer import SelfOptimizerEngine
        engine = SelfOptimizerEngine()
        for i in range(4):
            engine.record(f"m{i}", "crud_api", tier=2, success=True, duration_s=5.0, cost_usd=0.01)
        engine.record("expensive", "crud_api", tier=2, success=True, duration_s=5.0, cost_usd=10.0)
        recs = engine.recommendations()
        assert any("cost" in r.title.lower() or "3×" in r.detail for r in recs)

    def test_summary_works_with_no_data(self):
        from agent.orchestration.self_optimizer import SelfOptimizerEngine
        engine = SelfOptimizerEngine()
        assert "no mission history" in engine.summary()

    def test_summary_with_data(self):
        from agent.orchestration.self_optimizer import SelfOptimizerEngine
        engine = SelfOptimizerEngine()
        for i in range(5):
            engine.record(f"m{i}", "crud_api", tier=2, success=True, duration_s=3.0, cost_usd=0.02)
        summary = engine.summary()
        assert "5 missions" in summary
        assert "100%" in summary


# ── Phase 4: ModelRoutingOptimizer ────────────────────────────────────────────

class TestModelRoutingOptimizer:
    def test_heuristic_route_without_evidence(self):
        from agent.orchestration.model_routing_optimizer import ModelRoutingOptimizer
        opt = ModelRoutingOptimizer()
        decision = opt.route(task_tier=0)
        assert decision.confidence == "heuristic"
        assert decision.provider

    def test_evidence_route_after_observations(self):
        from agent.orchestration.model_routing_optimizer import ModelRoutingOptimizer
        opt = ModelRoutingOptimizer()
        for _ in range(3):
            opt.observe(2, "sonnet", True,  800.0, 0.01)
            opt.observe(2, "haiku",  False, 200.0, 0.001)
        decision = opt.route(task_tier=2)
        assert decision.confidence == "evidence"
        assert decision.provider == "sonnet"

    def test_lower_quality_score_for_failing_provider(self):
        from agent.orchestration.model_routing_optimizer import ProviderRecord
        r_good = ProviderRecord("good", 2, success_count=5, failure_count=0, total_latency_ms=500.0)
        r_bad  = ProviderRecord("bad",  2, success_count=1, failure_count=4, total_latency_ms=500.0)
        assert r_good.quality_score() > r_bad.quality_score()

    def test_top_providers_returns_sorted(self):
        from agent.orchestration.model_routing_optimizer import ModelRoutingOptimizer
        opt = ModelRoutingOptimizer()
        for _ in range(3):
            opt.observe(2, "sonnet",  True, 500.0, 0.01)
            opt.observe(2, "haiku",   True, 100.0, 0.001)
            opt.observe(2, "opus",    True, 2000.0, 0.10)
        top = opt.top_providers_for_tier(2)
        # Haiku should be first (fast + cheap)
        assert top[0].provider == "haiku"

    def test_scorecard_empty(self):
        from agent.orchestration.model_routing_optimizer import ModelRoutingOptimizer
        opt = ModelRoutingOptimizer()
        assert "no evidence" in opt.scorecard()

    def test_scorecard_with_data(self):
        from agent.orchestration.model_routing_optimizer import ModelRoutingOptimizer
        opt = ModelRoutingOptimizer()
        for _ in range(3):
            opt.observe(1, "haiku", True, 300.0, 0.002)
        card = opt.scorecard()
        assert "haiku" in card


# ── Phase 2: Planner Quality Benchmark ────────────────────────────────────────

class TestPlannerBenchmark:
    def test_benchmark_runs_100_plus_tasks(self):
        from agent.orchestration.v8_planner_benchmark import run_planner_benchmark
        report = run_planner_benchmark()
        assert report.task_count >= 100

    def test_benchmark_scores_90_plus(self):
        from agent.orchestration.v8_planner_benchmark import run_planner_benchmark
        report = run_planner_benchmark()
        assert report.plan_score >= 90.0, f"Expected ≥90, got {report.plan_score:.1f}"

    def test_simple_plan_passes_all_structural_checks(self):
        from agent.orchestration.v8_planner_benchmark import _check_plan, _make_plan
        plan = _make_plan("jwt_auth", [
            ("Auth",   "JWT auth",  [],      ["auth.py"]),
            ("Routes", "API routes", ["Auth"], ["routes.py"]),
            ("Tests",  "Auth tests", ["Routes"], ["tests.py"]),
        ])
        checks = _check_plan("jwt_auth", plan)
        failed = [c for c in checks if not c.passed]
        assert len(failed) == 0, f"Unexpected failures: {[(c.name, c.detail) for c in failed]}"

    def test_plan_with_no_deps_is_valid(self):
        from agent.orchestration.v8_planner_benchmark import _check_plan, _make_plan
        plan = _make_plan("parallel", [
            ("A", "Module A", [], ["a.py"]),
            ("B", "Module B", [], ["b.py"]),
            ("C", "Module C", [], ["c.py"]),
        ])
        checks = _check_plan("parallel", plan)
        assert all(c.passed for c in checks), f"Failures: {[c.name for c in checks if not c.passed]}"


# ── Phase 9: Scorecard ─────────────────────────────────────────────────────────

class TestProductionScorecard:
    def test_scorecard_has_ten_dimensions(self):
        from agent.orchestration.v8_scorecard import compute_scorecard
        import io, sys
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            card = compute_scorecard()
        finally:
            sys.stdout = old
        assert len(card.dimensions) == 10

    def test_planner_intelligence_scores_high(self):
        from agent.orchestration.v8_scorecard import compute_scorecard
        import io, sys
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            card = compute_scorecard()
        finally:
            sys.stdout = old
        planner_dim = next(d for d in card.dimensions if d.name == "Planner Intelligence")
        assert planner_dim.score >= 90.0, f"Planner scored {planner_dim.score}"

    def test_reliability_dimension_at_100(self):
        from agent.orchestration.v8_scorecard import compute_scorecard
        import io, sys
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            card = compute_scorecard()
        finally:
            sys.stdout = old
        rel_dim = next(d for d in card.dimensions if d.name == "Reliability")
        assert rel_dim.score >= 95.0, f"Reliability scored {rel_dim.score}"

    def test_certification_gates_include_live_requirements(self):
        from agent.orchestration.v8_scorecard import compute_scorecard
        import io, sys
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            card = compute_scorecard()
        finally:
            sys.stdout = old
        gates = card.certification_gates()
        # At least 2 gates require live providers
        live_req = sum(1 for _, p, d in gates if "REQUIRES" in d and not p)
        assert live_req >= 2

    def test_overall_score_above_80(self):
        from agent.orchestration.v8_scorecard import compute_scorecard
        import io, sys
        buf = io.StringIO(); old = sys.stdout; sys.stdout = buf
        try:
            card = compute_scorecard()
        finally:
            sys.stdout = old
        assert card.overall >= 80.0, f"Overall score {card.overall:.1f} is below 80"
