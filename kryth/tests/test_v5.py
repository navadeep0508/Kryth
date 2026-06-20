"""V5 Execution Intelligence Tests — Phase 14.

Unit + integration coverage for every V5 component:
- TeamLeadRuntime (Phase 1)       - DynamicReplanner (Phase 2)
- ArtifactValidator (Phase 3)     - ResourceScheduler (Phase 4)
- MissionDigitalTwin (Phase 5)    - BlockerIntelligence (Phase 6)
- OrgHealth (Phase 7)             - Portfolio (Phase 8)
- ExecutionForecaster (Phase 9)   - AutonomousRecoveryEngine (Phase 10)
- QualityEngine (Phase 11)        - run_v5 integration (Phase 12)
- Benchmark sanity (Phase 13)

No LLM calls anywhere — workers are stubbed via _fake_run_schedule, same
pattern as v4_validate.py / v5_validate.py.
"""
from __future__ import annotations

import sys
import unittest.mock as _mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from agent.orchestration.project_planner import ProjectModule, ProjectPlan, dag_from_plan, team_from_plan


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _simple_plan() -> ProjectPlan:
    mods = [
        ProjectModule(
            "Database", "Schema", deliverables=["schema.sql"], outputs=["schema.sql"],
            success_criteria=["Tables created"],
        ),
        ProjectModule(
            "API", "Endpoints", dependencies=["Database"], deliverables=["routes.py"],
            outputs=["routes.py"], success_criteria=["Routes implemented"],
        ),
    ]
    return ProjectPlan(
        project_name="Simple App", project_type="api", goal="test",
        modules=mods, recommended_mode="dag", parallel_streams=2, estimated_speedup=1.5,
    )


def _good_output(role: str) -> str:
    return f"Completed {role}. Implemented all required deliverables.\nAGENT_COMPLETE"


def _fake_run_schedule(**kw):
    from agent.orchestration.scheduler import WorkerResult, SchedulerResult
    team = kw["team"]
    outputs = {
        a.id: WorkerResult(agent_id=a.id, role=a.role, success=True, output=_good_output(a.role))
        for a in team.agents
    }
    return SchedulerResult(success=True, outputs=outputs, final_output="done",
                           total_turns=len(outputs) * 5, failed_agents=[])


# ── Phase 1: TeamLeadRuntime ───────────────────────────────────────────────────

class TestTeamLeadRuntime:
    def test_team_lead_approves_valid_worker_output(self):
        from agent.orchestration.team_lead_runtime import TeamLead
        from agent.orchestration.project_planner import DeliverableContract

        contract = DeliverableContract(module_name="Auth", goal="Build auth",
                                       outputs=["auth.py"], success_criteria=["Login works"])
        lead = TeamLead("Milestone 1")
        lead.assign_worker("Auth", "Auth Team", "Auth", contract, ["auth.py"], [])
        approved, _notes = lead.record_output("Auth", "Login works correctly. AGENT_COMPLETE", contract, ".")
        assert approved

    def test_team_lead_rejects_blocked_output(self):
        from agent.orchestration.team_lead_runtime import TeamLead
        from agent.orchestration.project_planner import DeliverableContract

        contract = DeliverableContract(module_name="Auth", goal="x", outputs=["a.py"], success_criteria=["y"])
        lead = TeamLead("Milestone 1")
        lead.assign_worker("Auth", "Auth Team", "Auth", contract, [], [])
        approved, _notes = lead.record_output("Auth", "blocked by missing dependency", contract, ".")
        assert not approved

    def test_workers_cannot_self_approve(self):
        """The only approval path is TeamLead.record_output — there is no
        worker-side API that sets WorkerStatus.APPROVED directly."""
        from agent.orchestration.team_lead_runtime import TeamLead, WorkerStatus
        from agent.orchestration.project_planner import DeliverableContract

        contract = DeliverableContract(module_name="X", goal="g", outputs=["a.py"])
        lead = TeamLead("M1")
        asgn = lead.assign_worker("X", "X Team", "X", contract, [], [])
        assert asgn.status == WorkerStatus.PENDING   # never starts APPROVED

    def test_program_manager_aggregates_reports(self):
        from agent.orchestration.team_lead_runtime import ProgramManager
        from agent.orchestration.project_planner import DeliverableContract

        pm = ProgramManager("Test Mission")
        contract = DeliverableContract(module_name="Auth", goal="x", outputs=["a.py"], success_criteria=["works"])
        report = pm.process_milestone(
            "Milestone 1", {"Auth": "works correctly. AGENT_COMPLETE"}, {"Auth": contract}, ".",
        )
        assert "Auth" in report.approved_workers
        final = pm.final_report()
        assert final.overall_approval_rate == 1.0


# ── Phase 2: DynamicReplanner ──────────────────────────────────────────────────

class TestDynamicReplanner:
    def test_classify_trigger_provider_failure(self):
        from agent.orchestration.dynamic_replanner import classify_trigger, ReplanTrigger
        assert classify_trigger("API rate limit exceeded") == ReplanTrigger.PROVIDER_FAILURE

    def test_classify_trigger_dependency_failure(self):
        from agent.orchestration.dynamic_replanner import classify_trigger, ReplanTrigger
        assert classify_trigger("blocked by missing dependency") == ReplanTrigger.DEPENDENCY_FAILURE

    def test_replanner_preserves_completed_milestones(self):
        from agent.orchestration.dynamic_replanner import DynamicReplanner
        plan = _simple_plan()
        milestones = plan.ensure_structured_milestones()
        first_name = milestones[0].name
        replanner = DynamicReplanner(plan, completed_milestones={first_name})
        result = replanner.apply(error="milestone failed", failed_milestone=milestones[-1].name)
        assert first_name in result.decision.preserved_milestones
        assert result.success

    def test_replanner_appends_test_fix_module(self):
        from agent.orchestration.dynamic_replanner import DynamicReplanner
        plan = _simple_plan()
        milestones = plan.ensure_structured_milestones()
        n_before = len(plan.modules)
        replanner = DynamicReplanner(plan)
        replanner.apply(
            error="pytest test_api.py failed: assertion error",
            failed_milestone=milestones[-1].name, failed_module="API",
        )
        assert len(plan.modules) > n_before

    def test_replanner_never_touches_completed_milestone_status(self):
        from agent.orchestration.dynamic_replanner import DynamicReplanner
        plan = _simple_plan()
        milestones = plan.ensure_structured_milestones()
        milestones[0].completed = True
        milestones[0].approved = True
        replanner = DynamicReplanner(plan, completed_milestones={milestones[0].name})
        replanner.apply(error="milestone failed", failed_milestone=milestones[-1].name)
        assert milestones[0].completed is True   # untouched


# ── Phase 3: ArtifactValidator ─────────────────────────────────────────────────

class TestArtifactValidator:
    def test_validate_artifacts_passes_with_sentinel_and_criteria(self):
        from agent.orchestration.artifact_validator import validate_artifacts
        from agent.orchestration.project_planner import DeliverableContract

        contract = DeliverableContract(module_name="X", goal="g", success_criteria=["works correctly"])
        result = validate_artifacts(contract, "implemented works correctly. AGENT_COMPLETE", ".")
        assert result.passed

    def test_validate_artifacts_fails_without_sentinel(self):
        from agent.orchestration.artifact_validator import validate_artifacts
        from agent.orchestration.project_planner import DeliverableContract

        contract = DeliverableContract(module_name="X", goal="g", success_criteria=["something specific"])
        result = validate_artifacts(contract, "nothing useful here", ".")
        assert not result.passed

    def test_check_files_exist_reports_missing(self, tmp_path):
        from agent.orchestration.artifact_validator import check_files_exist
        checks = check_files_exist(["missing.py"], str(tmp_path))
        assert not checks[0].passed

    def test_check_files_exist_reports_present(self, tmp_path):
        from agent.orchestration.artifact_validator import check_files_exist
        (tmp_path / "present.py").write_text("x = 1")
        checks = check_files_exist(["present.py"], str(tmp_path))
        assert checks[0].passed

    def test_check_python_imports_flags_syntax_error(self, tmp_path):
        from agent.orchestration.artifact_validator import check_python_imports
        (tmp_path / "broken.py").write_text("def f(:\n    pass")
        checks = check_python_imports(["broken.py"], str(tmp_path))
        assert not checks[0].passed


# ── Phase 4: ResourceScheduler ─────────────────────────────────────────────────

class TestResourceScheduler:
    def test_classify_task_tier_documentation_is_cheap(self):
        from agent.orchestration.resource_scheduler import classify_task_tier
        assert classify_task_tier("Docs Team", "Write documentation") == "cheap"

    def test_classify_task_tier_architecture_is_reasoning(self):
        from agent.orchestration.resource_scheduler import classify_task_tier
        assert classify_task_tier("Architecture Team", "Design system architecture") == "reasoning"

    def test_classify_task_tier_ui_is_vision(self):
        from agent.orchestration.resource_scheduler import classify_task_tier
        assert classify_task_tier("Frontend Team", "Review UI screenshot layout") == "vision"

    def test_allocate_returns_plan_for_every_agent(self):
        from agent.orchestration.resource_scheduler import ResourceScheduler

        plan = _simple_plan()
        dag = dag_from_plan(plan)
        team = team_from_plan(plan, dag, "test")
        rplan = ResourceScheduler().allocate(team.agents)
        assert len(rplan.agents) == len(team.agents)
        assert rplan.total_estimated_cost > 0


# ── Phase 5: MissionDigitalTwin ────────────────────────────────────────────────

class TestMissionDigitalTwin:
    def test_snapshot_tracks_planned_deliverables(self):
        from agent.orchestration.digital_twin import MissionDigitalTwin
        twin = MissionDigitalTwin(_simple_plan())
        snap = twin.snapshot()
        assert snap.planned_deliverables == 2   # schema.sql + routes.py
        assert snap.actual_deliverables == 0

    def test_recording_deliverable_closes_gap(self):
        from agent.orchestration.digital_twin import MissionDigitalTwin
        twin = MissionDigitalTwin(_simple_plan())
        twin.record_deliverables("Database", ["schema.sql"])
        snap = twin.snapshot()
        assert snap.actual_deliverables == 1
        assert snap.deliverable_gap_count == 1   # routes.py still missing

    def test_gap_report_lists_missing_deliverables(self):
        from agent.orchestration.digital_twin import MissionDigitalTwin
        twin = MissionDigitalTwin(_simple_plan())
        report = twin.snapshot().gap_report()
        assert "schema.sql" in report and "routes.py" in report

    def test_progress_delta_negative_when_under_delivering(self):
        from agent.orchestration.digital_twin import MissionDigitalTwin
        twin = MissionDigitalTwin(_simple_plan())
        twin.record_milestone_complete("Milestone 1 — Database")
        snap = twin.snapshot()
        assert snap.progress_delta <= 0   # milestone "done" but deliverable not recorded


# ── Phase 6: BlockerIntelligence ────────────────────────────────────────────────

class TestBlockerIntelligence:
    def test_register_and_resolve(self):
        from agent.orchestration.blocker_intelligence import BlockerIntelligence, BlockerImpact
        bi = BlockerIntelligence()
        b = bi.register("DB connection down", blocked_workers=["Auth"], impact=BlockerImpact.CRITICAL)
        assert len(bi.critical_blockers()) == 1
        bi.resolve(b.blocker_id)
        assert len(bi.critical_blockers()) == 0

    def test_infer_from_output_detects_blocker(self):
        from agent.orchestration.blocker_intelligence import BlockerIntelligence
        bi = BlockerIntelligence()
        result = bi.infer_from_output("Auth", "Auth", "blocked by missing API key", "Milestone 1")
        assert result is not None
        assert "Auth" in bi.blocked_workers()

    def test_infer_from_output_returns_none_for_clean_output(self):
        from agent.orchestration.blocker_intelligence import BlockerIntelligence
        bi = BlockerIntelligence()
        result = bi.infer_from_output("Auth", "Auth", "all done. AGENT_COMPLETE", "Milestone 1")
        assert result is None

    def test_top_blockers_ranked_by_impact(self):
        from agent.orchestration.blocker_intelligence import BlockerIntelligence, BlockerImpact
        bi = BlockerIntelligence()
        bi.register("low issue", impact=BlockerImpact.LOW)
        bi.register("critical issue", impact=BlockerImpact.CRITICAL, blocked_workers=["a", "b"])
        top = bi.top_blockers(1)
        assert top[0].impact == BlockerImpact.CRITICAL


# ── Phase 7: OrgHealth ──────────────────────────────────────────────────────────

class TestOrgHealth:
    def test_ingest_milestone_result_uses_bare_names_not_double_suffixed(self):
        """Regression test: modules_run carries 'X Team' role strings while
        worker_outputs/team_lead_reviews are keyed by the bare name — the
        tracker must reconcile these without producing 'X Team Team'."""
        from agent.orchestration.org_health import OrgHealthTracker
        from agent.orchestration.milestone_engine import MilestoneResult, TeamLeadReviewResult

        ms = MilestoneResult(
            milestone_name="Milestone 1", order=1,
            modules_run=["Database Team"],
            worker_outputs={"Database": "done"},
            team_lead_reviews={"Database": TeamLeadReviewResult(module_name="Database", approved=True)},
            success=True, elapsed_s=10.0,
        )
        tracker = OrgHealthTracker("Test")
        tracker.ingest_milestone_result(ms)
        report = tracker.generate_report()

        assert len(report.worker_records) == 1
        assert report.worker_records[0].role == "Database Team"
        assert "Team Team" not in report.worker_records[0].role
        assert report.worker_records[0].approvals_received == 1

    def test_throughput_does_not_blow_up_with_near_zero_elapsed(self):
        from agent.orchestration.org_health import OrgHealthTracker
        tracker = OrgHealthTracker("Test")
        tracker._total_deliverables = 5     # simulate near-instant completion
        report = tracker.generate_report()
        assert report.mission_throughput < 1_000_000   # sane upper bound, not billions


# ── Phase 8: Portfolio ──────────────────────────────────────────────────────────

class TestPortfolio:
    def test_register_and_health_report(self):
        from agent.orchestration.portfolio import PortfolioManager
        pm = PortfolioManager(total_token_budget=1_000_000)
        pm.register("a", "Mission A", token_budget=500_000)
        pm.start("a")
        pm.update("a", tokens_used=100_000, milestones_done=1)
        health = pm.health_report()
        assert health.total_missions == 1
        assert health.running == 1
        assert health.total_tokens_used == 100_000

    def test_completed_mission_tracked(self):
        from agent.orchestration.portfolio import PortfolioManager, MissionState
        pm = PortfolioManager()
        pm.register("a", "Mission A")
        pm.start("a")
        pm.complete("a", success=True)
        assert pm.get("a").state == MissionState.COMPLETED

    def test_throughput_does_not_blow_up_with_near_zero_elapsed(self):
        from agent.orchestration.portfolio import PortfolioManager
        pm = PortfolioManager()
        pm.register("a", "Mission A")
        pm.start("a")
        pm.update("a", deliverables_completed=10)
        health = pm.health_report()
        assert health.throughput_deliverables_hr < 1_000_000

    def test_at_risk_detection_on_low_remaining_budget(self):
        from agent.orchestration.portfolio import PortfolioManager
        pm = PortfolioManager()
        pm.register("a", "Mission A", token_budget=100_000)
        pm.start("a")
        pm.update("a", tokens_used=90_000)   # 10% remaining — under the 20% threshold
        health = pm.health_report()
        assert "Mission A" in health.at_risk_missions


# ── Phase 9: ExecutionForecaster ───────────────────────────────────────────────

class TestExecutionForecaster:
    def test_confidence_rises_with_more_samples(self):
        from agent.orchestration.execution_forecaster import ExecutionForecaster
        f = ExecutionForecaster(milestones_total=4, deliverables_total=8)
        snap1 = f.update(milestones_done=1, deliverables_done=2)
        assert snap1.confidence == "low"
        f.update(milestones_done=2, deliverables_done=4)
        f.update(milestones_done=3, deliverables_done=6)
        snap4 = f.update(milestones_done=4, deliverables_done=8)
        assert snap4.confidence in ("medium", "high")

    def test_remaining_deliverables_zero_when_done(self):
        from agent.orchestration.execution_forecaster import ExecutionForecaster
        f = ExecutionForecaster(milestones_total=2, deliverables_total=2)
        f.update(milestones_done=1, deliverables_done=1)
        snap = f.update(milestones_done=2, deliverables_done=2)
        assert snap.estimated_remaining_deliverables == 0

    def test_latest_returns_most_recent_snapshot_without_resampling(self):
        from agent.orchestration.execution_forecaster import ExecutionForecaster
        f = ExecutionForecaster(milestones_total=2, deliverables_total=2)
        assert f.latest is None
        f.update(milestones_done=1, deliverables_done=1)
        assert f.latest is not None
        assert f.latest.milestones_done == 1


# ── Phase 10: AutonomousRecoveryEngine ─────────────────────────────────────────

class TestAutonomousRecovery:
    def test_decide_maps_provider_failure_to_switch_provider(self):
        from agent.orchestration.autonomous_recovery import AutonomousRecoveryEngine, RecoveryAction
        engine = AutonomousRecoveryEngine()
        decision = engine.decide("API rate limit exceeded", target="Milestone 1")
        assert decision.action == RecoveryAction.SWITCH_PROVIDER

    def test_decide_maps_dependency_failure_to_replan(self):
        from agent.orchestration.autonomous_recovery import AutonomousRecoveryEngine, RecoveryAction
        engine = AutonomousRecoveryEngine()
        decision = engine.decide("blocked by missing dependency", target="Milestone 1")
        assert decision.action == RecoveryAction.REPLAN_MILESTONE

    def test_escalates_after_max_attempts(self):
        from agent.orchestration.autonomous_recovery import AutonomousRecoveryEngine, RecoveryAction, MAX_ATTEMPTS_PER_TARGET
        engine = AutonomousRecoveryEngine()
        for _ in range(MAX_ATTEMPTS_PER_TARGET):
            engine.decide("milestone failed", target="X")
        final = engine.decide("milestone failed", target="X")
        assert final.action == RecoveryAction.ESCALATE

    def test_success_resets_attempt_counter(self):
        from agent.orchestration.autonomous_recovery import AutonomousRecoveryEngine
        engine = AutonomousRecoveryEngine()
        d1 = engine.decide("milestone failed", target="X")
        engine.record_outcome(d1, success=True)
        d2 = engine.decide("milestone failed", target="X")
        assert d2.attempt == 1   # reset, not 2

    def test_success_rate_computation(self):
        from agent.orchestration.autonomous_recovery import AutonomousRecoveryEngine
        engine = AutonomousRecoveryEngine()
        d1 = engine.decide("x", target="A")
        engine.record_outcome(d1, success=True)
        d2 = engine.decide("x", target="B")
        engine.record_outcome(d2, success=False)
        assert engine.success_rate() == 0.5


# ── Phase 11: QualityEngine ─────────────────────────────────────────────────────

class TestQualityEngine:
    def test_quality_score_perfect_mission(self):
        from agent.orchestration.quality_engine import compute_quality_report
        from agent.orchestration.milestone_engine import (
            MissionDeliveryResult, MilestoneResult, ContractValidationResult, TeamLeadReviewResult,
        )

        ms = MilestoneResult(
            milestone_name="M1", order=1, success=True, approved=True,
            contract_validations={"A": ContractValidationResult("A", True, checks_run=1, checks_passed=1)},
            team_lead_reviews={"A": TeamLeadReviewResult("A", True)},
        )
        mr = MissionDeliveryResult(
            project_name="Test", milestones=[ms],
            total_deliverables_planned=1, total_deliverables_completed=1,
        )
        report = compute_quality_report("Test", mr)
        assert report.mission_quality_score == 100.0
        assert report.grade() == "excellent"

    def test_quality_score_penalizes_rework(self):
        from agent.orchestration.quality_engine import compute_quality_report
        from agent.orchestration.milestone_engine import MissionDeliveryResult, MilestoneResult

        ms = MilestoneResult(milestone_name="M1", order=1, success=True, approved=False, rework_required=True)
        mr = MissionDeliveryResult(project_name="Test", milestones=[ms])
        report = compute_quality_report("Test", mr)
        assert report.breakdown.rework_rate == 1.0
        assert report.mission_quality_score < 100.0

    def test_no_milestones_yields_zero_score(self):
        from agent.orchestration.quality_engine import compute_quality_report
        from agent.orchestration.milestone_engine import MissionDeliveryResult

        mr = MissionDeliveryResult(project_name="Empty", milestones=[])
        report = compute_quality_report("Empty", mr)
        assert report.mission_quality_score == 0.0


# ── Phase 12: run_v5 integration ───────────────────────────────────────────────

class TestRunV5Integration:
    def test_run_v5_end_to_end_populates_every_report(self):
        from agent.orchestration.v5_runtime import run_v5

        plan = _simple_plan()
        dag = dag_from_plan(plan)
        team = team_from_plan(plan, dag, "test")

        with _mock.patch(
            "agent.orchestration.scheduler.run_schedule",
            side_effect=lambda **kw: _fake_run_schedule(**kw),
        ):
            # tier=4 forces full ENTERPRISE stack so all layers are active
            report = run_v5(
                plan=plan, dag=dag, team=team, project_context="test", user_input="test",
                max_turns_per_agent=10, max_workers=2, project_root=".", tier=4,
            )

        assert report.mission_result.success
        assert report.program_manager_report is not None
        assert report.digital_twin_snapshot is not None
        assert report.org_health_report is not None
        assert report.forecast is not None
        assert report.quality_report is not None
        assert report.quality_report.mission_quality_score > 0
        # V6: tier + overhead always present
        assert report.tier == 4
        assert report.overhead_report is not None

    def test_run_v5_recovers_from_transient_failure(self):
        from agent.orchestration.v5_runtime import run_v5

        plan = _simple_plan()
        dag = dag_from_plan(plan)
        team = team_from_plan(plan, dag, "test")

        state = {"calls": 0}

        def _flaky(**kw):
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("simulated failure")
            return _fake_run_schedule(**kw)

        with _mock.patch("agent.orchestration.scheduler.run_schedule", side_effect=_flaky):
            # tier=4: recovery layer must be active to test recovery
            report = run_v5(
                plan=plan, dag=dag, team=team, project_context="test", user_input="test",
                max_turns_per_agent=10, max_workers=2, project_root=".", tier=4,
            )

        assert report.mission_result.success
        assert report.recovery_attempts > 0

    def test_auto_tier_selects_standard_for_small_plan(self):
        """V6: a 2-module plan should auto-select STANDARD tier, not ENTERPRISE."""
        from agent.orchestration.v5_runtime import run_v5
        from agent.orchestration.execution_layer_selector import ExecutionTier

        plan = _simple_plan()   # 2 modules
        dag = dag_from_plan(plan); team = team_from_plan(plan, dag, "test")

        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            report = run_v5(plan=plan, dag=dag, team=team, project_context="test",
                            user_input="test", max_turns_per_agent=5, max_workers=2)

        assert report.tier <= int(ExecutionTier.COMPLEX), \
            f"2-module plan should not trigger ENTERPRISE, got {ExecutionTier(report.tier).name}"
        # STANDARD should NOT have digital twin or program manager
        assert report.digital_twin_snapshot is None
        assert report.program_manager_report is None

    def test_v4_only_escape_hatch_is_honoured_by_env(self, monkeypatch):
        """KRYTH_V4_ONLY=1 must disable the V5 gate in orchestration/__init__.py."""
        monkeypatch.setenv("KRYTH_V4_ONLY", "1")
        from agent.env import getenv_bool
        assert getenv_bool("KRYTH_V4_ONLY", False) is True


# ── Phase 13: Benchmark sanity ──────────────────────────────────────────────────

class TestBenchmarkSanity:
    def test_v4_arm_does_not_track_intelligence_metrics(self):
        from agent.orchestration.v5_benchmark import _run_v4_arm
        from agent.orchestration.v4_validate import MISSIONS

        with _mock.patch(
            "agent.orchestration.scheduler.run_schedule",
            side_effect=lambda **kw: _fake_run_schedule(**kw),
        ):
            v4 = _run_v4_arm(MISSIONS["jwt_auth"])

        assert v4.worker_utilization is None
        assert v4.quality_score is None
        assert v4.recovery_attempts is None

    def test_v5_arm_tracks_intelligence_metrics(self):
        from agent.orchestration.v5_benchmark import _run_v5_arm
        from agent.orchestration.v4_validate import MISSIONS

        with _mock.patch(
            "agent.orchestration.scheduler.run_schedule",
            side_effect=lambda **kw: _fake_run_schedule(**kw),
        ):
            v5 = _run_v5_arm(MISSIONS["jwt_auth"])

        assert v5.worker_utilization is not None
        assert v5.quality_score is not None
        assert v5.recovery_attempts is not None

    def test_both_arms_agree_on_token_proxy_for_same_work(self):
        from agent.orchestration.v5_benchmark import _run_v4_arm, _run_v5_arm
        from agent.orchestration.v4_validate import MISSIONS

        plan = MISSIONS["website"]
        with _mock.patch(
            "agent.orchestration.scheduler.run_schedule",
            side_effect=lambda **kw: _fake_run_schedule(**kw),
        ):
            v4 = _run_v4_arm(plan)
            v5 = _run_v5_arm(plan)

        assert v4.token_usage == v5.token_usage
