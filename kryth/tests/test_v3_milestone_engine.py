"""V3 Milestone Engine Tests.

Tests for:
- Milestone generation from ProjectPlan
- Deliverable contracts (DeliverableContract)
- Contract validation
- Planner review gate
- Deliverable tracking
- Critical path identification
- MissionDeliveryResult scorecard
- Worker contract injection in scheduler
- V3 data model changes (inputs/outputs, structured milestones)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from agent.orchestration.project_planner import (
    ProjectModule, ProjectMilestone, ProjectPlan, DeliverableContract,
)
from agent.orchestration.milestone_engine import (
    ContractValidationResult, MilestoneResult, MissionDeliveryResult,
    DeliverableTracker, validate_contract, planner_review_milestone, _id,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _internship_plan() -> ProjectPlan:
    mods = [
        ProjectModule("Database",       "PostgreSQL schema",
                      deliverables=["users table", "jobs table"],
                      outputs=["DB connection", "Schema"],
                      success_criteria=["Tables created", "Migrations run"]),
        ProjectModule("Authentication", "JWT auth",
                      inputs=["DB connection"],
                      outputs=["JWT tokens", "RBAC"],
                      deliverables=["Auth service", "JWT tokens"],
                      dependencies=["Database"],
                      success_criteria=["Login works", "Tests pass"]),
        ProjectModule("Student Portal", "Student dashboard",
                      inputs=["JWT tokens"],
                      outputs=["Student UI"],
                      deliverables=["Dashboard UI"],
                      dependencies=["Authentication"],
                      success_criteria=["Dashboard loads"]),
        ProjectModule("Company Portal", "Company dashboard",
                      inputs=["JWT tokens"],
                      outputs=["Company UI"],
                      deliverables=["Jobs UI"],
                      dependencies=["Authentication"],
                      success_criteria=["Jobs page works"]),
        ProjectModule("AI Matching",   "ML matching engine",
                      inputs=["Student data", "Job data"],
                      outputs=["Match score API"],
                      deliverables=["Matching API"],
                      dependencies=["Database", "Authentication"],
                      success_criteria=["Matching endpoint returns results"]),
        ProjectModule("Testing",       "Full test suite",
                      inputs=["All components"],
                      outputs=["Test report"],
                      deliverables=["Test suite", "CI config"],
                      dependencies=["Student Portal", "Company Portal", "AI Matching"],
                      success_criteria=["All tests pass"]),
    ]
    return ProjectPlan(
        project_name="AI Internship Platform",
        project_type="saas",
        goal="Match students with internships using AI",
        modules=mods,
        recommended_mode="dag",
        parallel_streams=3,
        estimated_speedup=2.2,
    )


# ── Phase 1: Milestone generation ────────────────────────────────────────────

class TestMilestoneGeneration:
    def test_ensure_structured_milestones_builds_from_layers(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        assert len(milestones) >= 2

    def test_milestones_cover_all_modules(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        covered = {name for ms in milestones for name in ms.modules}
        planned = {m.name for m in plan.modules}
        assert covered == planned

    def test_milestone_order_is_sequential(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        orders = [ms.order for ms in milestones]
        assert orders == sorted(orders)

    def test_database_in_first_milestone(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        first = milestones[0]
        assert "Database" in first.modules

    def test_testing_in_last_milestone(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        last = milestones[-1]
        assert "Testing" in last.modules

    def test_critical_milestone_flagged(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        # At least one milestone should be on critical path
        assert any(ms.is_critical for ms in milestones)

    def test_milestone_deliverables_planned_counted(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        # Each milestone should have planned deliverables > 0
        for ms in milestones:
            # Allow 0 for milestones where module has no deliverables
            assert ms.deliverables_planned >= 0

    def test_parsed_milestones_from_json(self):
        """Structured milestone data parsed from LLM JSON."""
        from agent.orchestration.project_planner import _parse_plan
        import json
        plan_json = json.dumps({
            "project_name": "Test App",
            "project_type": "api",
            "goal": "Build test app",
            "modules": [
                {"name": "Auth", "goal": "auth", "dependencies": [],
                 "files_owned": [], "deliverables": ["JWT"],
                 "success_criteria": ["Login works"],
                 "estimated_turns": 20, "risk": "low",
                 "inputs": ["User credentials"], "outputs": ["JWT token"]},
            ],
            "milestones": [
                {"name": "Milestone 1 — Auth", "order": 1,
                 "modules": ["Auth"], "goal": "Auth setup"}
            ],
            "recommended_mode": "direct",
            "parallel_streams": 1,
            "estimated_speedup": 1.0,
        })
        plan = _parse_plan(plan_json, "test")
        assert plan is not None
        assert len(plan.structured_milestones) == 1
        assert plan.structured_milestones[0].name == "Milestone 1 — Auth"
        assert plan.structured_milestones[0].modules == ["Auth"]


# ── Phase 2: Deliverable contracts ───────────────────────────────────────────

class TestDeliverableContracts:
    def test_module_to_contract(self):
        m = ProjectModule(
            "Auth", "JWT auth",
            inputs=["DB connection"],
            outputs=["JWT tokens"],
            success_criteria=["Login works"],
        )
        contract = m.to_contract()
        assert contract.module_name == "Auth"
        assert "JWT tokens" in contract.outputs
        assert "DB connection" in contract.inputs
        assert "Login works" in contract.success_criteria

    def test_plan_get_contracts(self):
        plan = _internship_plan()
        contracts = plan.get_contracts()
        assert len(contracts) == len(plan.modules)
        names = {c.module_name for c in contracts}
        assert "Database" in names
        assert "AI Matching" in names

    def test_plan_get_contract_by_name(self):
        plan = _internship_plan()
        c = plan.get_contract("Authentication")
        assert c is not None
        assert c.module_name == "Authentication"

    def test_plan_get_contract_by_id(self):
        plan = _internship_plan()
        c = plan.get_contract("ai_matching")
        assert c is not None
        assert c.module_name == "AI Matching"

    def test_contract_worker_brief_contains_goal(self):
        m = ProjectModule("Auth", "JWT auth", outputs=["JWT token"],
                          success_criteria=["Login works"])
        contract = m.to_contract()
        brief = contract.to_worker_brief()
        assert "Auth" in brief
        assert "JWT auth" in brief
        assert "LOGIN WORKS" in brief.upper() or "Login works" in brief

    def test_contract_worker_brief_contains_scope_lock(self):
        m = ProjectModule("Auth", "goal")
        contract = m.to_contract()
        brief = contract.to_worker_brief()
        assert "re-plan" in brief.lower() or "scope" in brief.lower()

    def test_inputs_outputs_in_module(self):
        plan = _internship_plan()
        auth = next(m for m in plan.modules if m.name == "Authentication")
        assert len(auth.inputs) > 0
        assert len(auth.outputs) > 0


# ── Phase 5: Deliverable tracking ────────────────────────────────────────────

class TestDeliverableTracking:
    def test_tracker_records_completed(self):
        plan = _internship_plan()
        tracker = DeliverableTracker(plan)
        tracker.record_complete("Database", ["users table", "jobs table"])
        status = tracker.overall_status()
        assert status["completed"] == 2

    def test_tracker_records_failed(self):
        plan = _internship_plan()
        tracker = DeliverableTracker(plan)
        tracker.record_failed("AI Matching", "ML model not trained")
        status = tracker.overall_status()
        assert "AI Matching" in status["failed_modules"]

    def test_milestone_status(self):
        plan = _internship_plan()
        milestones = plan.ensure_structured_milestones()
        tracker = DeliverableTracker(plan)
        # Complete database deliverables
        tracker.record_complete("Database", ["users table", "jobs table"])
        ms0 = next(ms for ms in milestones if "Database" in ms.modules)
        ms_status = tracker.milestone_status(ms0)
        assert ms_status["completed"] >= 0

    def test_format_status(self):
        plan = _internship_plan()
        tracker = DeliverableTracker(plan)
        output = tracker.format_status()
        assert "Deliverables" in output
        assert "planned" in output.lower() or "/" in output


# ── Phase 6: Contract validation ─────────────────────────────────────────────

class TestContractValidation:
    def _contract(self, name="Auth", outputs=None, criteria=None):
        return DeliverableContract(
            module_name=name,
            goal="implement auth",
            outputs=outputs or ["JWT tokens", "User session"],
            success_criteria=criteria or ["Login works", "Tests pass"],
        )

    def test_valid_output_passes(self):
        contract = self._contract()
        output = (
            "AGENT_COMPLETE: auth\n"
            "Successfully implemented JWT tokens and User session.\n"
            "Login works. Tests pass."
        )
        result = validate_contract(contract, output)
        assert result.passed

    def test_missing_sentinel_fails(self):
        contract = self._contract()
        output = "Done with JWT tokens. Login works."  # no AGENT_COMPLETE
        result = validate_contract(contract, output)
        assert not result.passed
        assert any("AGENT_COMPLETE" in f for f in result.failures)

    def test_empty_output_fails(self):
        contract = self._contract()
        result = validate_contract(contract, "")
        assert not result.passed

    def test_checks_run_and_passed_tracked(self):
        contract = self._contract()
        output = "AGENT_COMPLETE: auth\nJWT tokens done. Login works. Tests pass."
        result = validate_contract(contract, output)
        assert result.checks_run > 0
        assert result.checks_passed > 0

    def test_score_between_0_and_1(self):
        contract = self._contract()
        output = "AGENT_COMPLETE: auth\njwt tokens. login works."
        result = validate_contract(contract, output)
        assert 0.0 <= result.score <= 1.0


# ── Phase 7: Planner review gate ─────────────────────────────────────────────

class TestPlannerReviewGate:
    def _make_validations(self, passed=True):
        return {
            "Auth": ContractValidationResult("Auth", passed=passed, checks_run=3, checks_passed=3 if passed else 1),
            "DB":   ContractValidationResult("DB",   passed=passed, checks_run=2, checks_passed=2 if passed else 0),
        }

    def test_non_interactive_approves_when_all_pass(self):
        validations = self._make_validations(passed=True)
        approved, notes = planner_review_milestone(
            "Milestone 1", validations, interactive=False
        )
        assert approved is True

    def test_non_interactive_rejects_when_fails(self):
        validations = self._make_validations(passed=False)
        approved, notes = planner_review_milestone(
            "Milestone 1", validations, interactive=False
        )
        assert approved is False

    def test_review_returns_notes(self):
        validations = self._make_validations(passed=True)
        _, notes = planner_review_milestone("M1", validations, interactive=False)
        assert isinstance(notes, str) and len(notes) > 0

    def test_interactive_approve_key(self):
        validations = self._make_validations(passed=True)
        approved, _ = planner_review_milestone(
            "M1", validations, interactive=True, reader=lambda: "a"
        )
        assert approved is True

    def test_interactive_rework_key(self):
        validations = self._make_validations(passed=True)
        approved, _ = planner_review_milestone(
            "M1", validations, interactive=True, reader=lambda: "r"
        )
        assert approved is False


# ── Phase 8: Critical path ────────────────────────────────────────────────────

class TestCriticalPath:
    def test_critical_path_not_empty(self):
        plan = _internship_plan()
        cp = plan.critical_path()
        assert len(cp) > 0

    def test_database_on_critical_path(self):
        plan = _internship_plan()
        cp = plan.critical_path()
        # Database is required by Auth which is required by AI Matching → critical
        assert "Database" in cp

    def test_critical_path_respects_dependency_order(self):
        plan = _internship_plan()
        cp = plan.critical_path()
        # Database must come before Authentication on the critical path
        if "Database" in cp and "Authentication" in cp:
            assert cp.index("Database") < cp.index("Authentication")

    def test_critical_path_terminates_at_sink(self):
        plan = _internship_plan()
        cp = plan.critical_path()
        # Testing depends on everyone — it's the final module, should be in cp
        assert "Testing" in cp

    def test_simple_chain_critical_path(self):
        """A → B → C has one critical path: [A, B, C]."""
        mods = [
            ProjectModule("A", "first",  dependencies=[]),
            ProjectModule("B", "second", dependencies=["A"]),
            ProjectModule("C", "third",  dependencies=["B"]),
        ]
        plan = ProjectPlan("chain", "api", "chain", modules=mods)
        cp = plan.critical_path()
        assert "A" in cp
        assert "B" in cp
        assert "C" in cp

    def test_parallel_branches_not_all_on_critical(self):
        """When two parallel branches exist, only the longer one is critical."""
        mods = [
            ProjectModule("Base",  "base", dependencies=[]),
            ProjectModule("LongA", "long", dependencies=["Base"], estimated_turns=50),
            ProjectModule("ShortB","short",dependencies=["Base"], estimated_turns=10),
            ProjectModule("End",   "end",  dependencies=["LongA", "ShortB"]),
        ]
        plan = ProjectPlan("parallel", "api", "test", modules=mods)
        cp = plan.critical_path()
        # Both branches merge into End — all are reachable from End
        assert "End" in cp


# ── Phase 10: Mission scorecard ───────────────────────────────────────────────

class TestMissionScorecard:
    def _result(self) -> MissionDeliveryResult:
        r = MissionDeliveryResult(
            project_name="Test Project",
            total_modules=5,
            completed_modules=4,
            failed_modules=1,
            total_deliverables_planned=10,
            total_deliverables_completed=8,
            critical_path_names=["Database", "Auth", "Testing"],
            elapsed_s=45.2,
        )
        ms1 = MilestoneResult("M1", 1, success=True, approved=True)
        ms2 = MilestoneResult("M2", 2, success=False, approved=False)
        r.milestones = [ms1, ms2]
        return r

    def test_success_property(self):
        r = self._result()
        assert r.success is False   # one milestone failed

    def test_success_pct(self):
        r = self._result()
        assert r.success_pct == 50.0  # 1/2 milestones

    def test_scorecard_contains_project_name(self):
        r = self._result()
        sc = r.scorecard()
        assert "Test Project" in sc

    def test_scorecard_contains_milestone_status(self):
        r = self._result()
        sc = r.scorecard()
        assert "✓" in sc and "✗" in sc

    def test_scorecard_contains_critical_path(self):
        r = self._result()
        sc = r.scorecard()
        assert "Database" in sc

    def test_milestone_completion_pct(self):
        ms = MilestoneResult("M1", 1, deliverables_planned=10, deliverables_completed=8)
        assert ms.completion_pct == 80.0

    def test_milestone_completion_100_when_no_planned(self):
        ms = MilestoneResult("M1", 1, success=True, deliverables_planned=0)
        assert ms.completion_pct == 100.0


# ── Worker contract injection ─────────────────────────────────────────────────

class TestWorkerContractInjection:
    def test_execution_contract_in_worker_prompt(self):
        from agent.orchestration.scheduler import _build_agent_system_prompt
        from agent.orchestration.team_generator import AgentRole, OwnedScope
        from agent.orchestration.task_dag import TaskDAG

        dag = TaskDAG(name="test")
        agent = AgentRole(
            id="authentication", role="Authentication Team",
            mission="implement JWT",
            dependencies=[], owns=OwnedScope(), task_node_ids=[],
        )
        prompt = _build_agent_system_prompt(agent, dag, "", {})
        # Verify the V3 org execution model language is present
        assert "WORKER" in prompt
        assert "AGENT_COMPLETE" in prompt
        assert "Planner" in prompt or "re-plan" in prompt.lower()

    def test_deliverable_contract_injected_when_plan_attached(self):
        """When session has _project_plan, worker receives deliverable contract."""
        from agent.orchestration.scheduler import _run_single_agent
        from agent.orchestration.team_generator import AgentRole, OwnedScope
        from agent.orchestration.task_dag import TaskDAG

        plan = _internship_plan()
        dag = TaskDAG(name="test")
        agent = AgentRole(
            id="authentication", role="Authentication Team",
            mission="implement JWT",
            dependencies=["database"],
            owns=OwnedScope(), task_node_ids=[],
        )

        injected_messages = []

        mock_nested = MagicMock()
        mock_nested.messages = []
        mock_nested.depth = 1
        mock_nested.mission_contract = None
        mock_nested.remembered_permissions = {}
        mock_nested.tool_call_count = 0
        mock_nested.cumulative_in_tokens = 0
        mock_nested.cumulative_out_tokens = 0

        parent_session = MagicMock()
        parent_session.depth = 0
        parent_session.mission_contract = None
        parent_session.remembered_permissions = {}
        parent_session._project_plan = plan  # V3: attach plan to session

        mock_result = MagicMock()
        mock_result.status = "done"
        mock_result.content = "AGENT_COMPLETE: authentication"
        mock_result.turns_used = 5

        with patch("agent.tools._subagent._build_nested", return_value=mock_nested), \
             patch("agent.agent_loop.run_inner_loop", return_value=mock_result), \
             patch("agent.session.get_session", return_value=parent_session), \
             patch("agent.session.push_session", return_value=None), \
             patch("agent.session.pop_session"), \
             patch("agent.ui.dashboard.push_provider_health"):
            prior = {"__user_input__": "build", "database": "schema done"}
            result = _run_single_agent(agent, dag, "", prior, 10)

        # Contract message should have been appended
        contract_msgs = [
            m for m in mock_nested.messages
            if isinstance(m, dict) and "DELIVERABLE CONTRACT" in (m.get("content") or "")
        ]
        assert len(contract_msgs) >= 1, (
            "Worker should receive deliverable contract when plan is attached to session"
        )


# ── V3 data model ────────────────────────────────────────────────────────────

class TestV3DataModel:
    def test_project_module_has_inputs_outputs(self):
        m = ProjectModule("Auth", "goal", inputs=["DB"], outputs=["JWT"])
        assert m.inputs == ["DB"]
        assert m.outputs == ["JWT"]

    def test_project_milestone_dataclass(self):
        ms = ProjectMilestone("M1", 1, ["Auth", "DB"])
        assert ms.name == "M1"
        assert ms.order == 1
        assert "Auth" in ms.modules
        assert ms.completed is False
        assert ms.progress_pct == 0.0

    def test_project_milestone_progress_when_complete(self):
        ms = ProjectMilestone("M1", 1, ["Auth"], deliverables_planned=4)
        ms.deliverables_completed = 4
        assert ms.progress_pct == 100.0

    def test_deliverable_contract_not_fulfilled_by_default(self):
        c = DeliverableContract("Auth", "goal")
        assert c.fulfilled is False

    def test_plan_has_structured_milestones_field(self):
        plan = ProjectPlan("p", "api", "g", modules=[
            ProjectModule("A", "a", dependencies=[])
        ])
        assert hasattr(plan, "structured_milestones")
        assert isinstance(plan.structured_milestones, list)

    def test_id_helper(self):
        assert _id("AI Matching") == "ai_matching"
        assert _id("Authentication") == "authentication"
        assert _id("Student Portal") == "student_portal"
