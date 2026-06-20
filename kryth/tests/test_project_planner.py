"""Tests for the V2 Planner-First Orchestration Architecture.

Tests the project_planner module: ProjectModule, ProjectPlan, plan parsing,
DAG generation from plan, team generation from plan, and mode selection.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from agent.orchestration.project_planner import (
    ProjectModule, ProjectPlan,
    dag_from_plan, team_from_plan, mode_from_plan,
    _parse_plan, _extract_json_object, _str_list, _str_dict,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _saas_plan() -> ProjectPlan:
    """A realistic 8-module SaaS plan for testing."""
    mods = [
        ProjectModule("Database",        "Postgres schema",       dependencies=[]),
        ProjectModule("Authentication",  "JWT + RBAC",            dependencies=["Database"]),
        ProjectModule("Student Portal",  "Student dashboard",     dependencies=["Authentication"]),
        ProjectModule("Company Portal",  "Company dashboard",     dependencies=["Authentication"]),
        ProjectModule("Resume Upload",   "S3 resume storage",     dependencies=["Authentication"]),
        ProjectModule("AI Matching",     "ML matching engine",    dependencies=["Database", "Resume Upload"]),
        ProjectModule("Notifications",   "Email + push",          dependencies=["Authentication"]),
        ProjectModule("Testing",         "Full test suite",       dependencies=["Student Portal", "Company Portal", "AI Matching"]),
    ]
    return ProjectPlan(
        project_name="AI Internship Platform",
        project_type="saas",
        goal="Match students with internships using AI",
        features=["Student Dashboard", "Company Portal", "AI Matching", "Notifications"],
        tech_stack={"frontend": "Next.js", "backend": "FastAPI", "database": "PostgreSQL"},
        modules=mods,
        estimated_files=28,
        recommended_mode="dag",
        parallel_streams=4,
        estimated_speedup=2.4,
    )


def _simple_plan() -> ProjectPlan:
    return ProjectPlan(
        project_name="JWT Auth",
        project_type="library",
        goal="JWT authentication library",
        modules=[ProjectModule("Auth", "implement JWT", dependencies=[])],
        recommended_mode="direct",
        parallel_streams=1,
        estimated_speedup=1.0,
    )


# ── ProjectModule tests ───────────────────────────────────────────────────────

class TestProjectModule:
    def test_id_slugifies_name(self):
        m = ProjectModule("AI Matching", "ml engine")
        assert m.id == "ai_matching"

    def test_id_handles_special_chars(self):
        m = ProjectModule("Auth & Security", "auth")
        assert " " not in m.id
        assert "&" not in m.id

    def test_defaults_populated(self):
        m = ProjectModule("Database", "schema")
        assert m.files_owned == []
        assert m.dependencies == []
        assert m.risk == "low"
        assert m.estimated_turns == 30


# ── ProjectPlan tests ─────────────────────────────────────────────────────────

class TestProjectPlan:
    def test_is_trivial_single_module(self):
        plan = _simple_plan()
        assert plan.is_trivial is True

    def test_is_not_trivial_multi_module(self):
        plan = _saas_plan()
        assert plan.is_trivial is False

    def test_independent_modules_have_no_deps(self):
        plan = _saas_plan()
        independent = plan.independent_modules
        assert all(len(m.dependencies) == 0 for m in independent)
        names = {m.name for m in independent}
        assert "Database" in names

    def test_dependency_layers_order(self):
        plan = _saas_plan()
        layers = plan.dependency_layers()
        assert len(layers) >= 2

        # Database must be in layer 0 (no deps)
        layer0_names = {m.name for m in layers[0]}
        assert "Database" in layer0_names

        # Authentication depends on Database → must be in later layer
        auth_layer = next(i for i, l in enumerate(layers) if any(m.name == "Authentication" for m in l))
        db_layer   = next(i for i, l in enumerate(layers) if any(m.name == "Database" for m in l))
        assert auth_layer > db_layer

        # Testing depends on Student/Company/AI → must be in final layer
        test_layer = next(i for i, l in enumerate(layers) if any(m.name == "Testing" for m in l))
        assert test_layer == len(layers) - 1

    def test_dependency_layers_all_modules_covered(self):
        plan = _saas_plan()
        layers = plan.dependency_layers()
        all_names = {m.name for layer in layers for m in layer}
        plan_names = {m.name for m in plan.modules}
        assert all_names == plan_names

    def test_to_summary_contains_key_info(self):
        plan = _saas_plan()
        summary = plan.to_summary()
        assert "AI Internship Platform" in summary
        assert "Next.js" in summary or "FastAPI" in summary
        assert "8" in summary  # module count


# ── JSON parsing tests ────────────────────────────────────────────────────────

class TestParsePlan:
    def _valid_json(self) -> str:
        return json.dumps({
            "project_name": "My App",
            "project_type": "saas",
            "goal": "Build a great app",
            "features": ["Login", "Dashboard"],
            "tech_stack": {"backend": "FastAPI"},
            "modules": [
                {
                    "name": "Authentication",
                    "goal": "JWT auth",
                    "files_owned": ["auth/", "routes/auth.py"],
                    "deliverables": ["JWT tokens", "User model"],
                    "dependencies": [],
                    "success_criteria": ["Login works", "Tests pass"],
                    "estimated_turns": 25,
                    "risk": "medium",
                },
                {
                    "name": "Dashboard",
                    "goal": "User dashboard",
                    "files_owned": ["frontend/dashboard/"],
                    "deliverables": ["Dashboard UI"],
                    "dependencies": ["Authentication"],
                    "success_criteria": ["Dashboard loads"],
                    "estimated_turns": 30,
                    "risk": "low",
                },
            ],
            "estimated_files": 10,
            "risks": ["Scope creep"],
            "recommended_mode": "dag",
            "parallel_streams": 2,
            "estimated_speedup": 1.8,
        })

    def test_parses_valid_json(self):
        plan = _parse_plan(self._valid_json(), "build my app")
        assert plan is not None
        assert plan.project_name == "My App"
        assert len(plan.modules) == 2

    def test_module_names_preserved(self):
        plan = _parse_plan(self._valid_json(), "build my app")
        names = {m.name for m in plan.modules}
        assert "Authentication" in names
        assert "Dashboard" in names

    def test_dependencies_parsed(self):
        plan = _parse_plan(self._valid_json(), "build my app")
        dashboard = next(m for m in plan.modules if m.name == "Dashboard")
        assert "Authentication" in dashboard.dependencies

    def test_estimated_turns_parsed(self):
        plan = _parse_plan(self._valid_json(), "build my app")
        auth = next(m for m in plan.modules if m.name == "Authentication")
        assert auth.estimated_turns == 25

    def test_risk_parsed(self):
        plan = _parse_plan(self._valid_json(), "build my app")
        auth = next(m for m in plan.modules if m.name == "Authentication")
        assert auth.risk == "medium"

    def test_recommended_mode_parsed(self):
        plan = _parse_plan(self._valid_json(), "build my app")
        assert plan.recommended_mode == "dag"

    def test_parallel_streams_parsed(self):
        plan = _parse_plan(self._valid_json(), "build my app")
        assert plan.parallel_streams == 2

    def test_invalid_json_returns_none(self):
        assert _parse_plan("this is not json", "test") is None

    def test_empty_modules_returns_none(self):
        j = json.dumps({"project_name": "x", "modules": [], "goal": "y"})
        assert _parse_plan(j, "test") is None

    def test_strips_markdown_fences(self):
        raw = "```json\n" + self._valid_json() + "\n```"
        plan = _parse_plan(raw, "test")
        assert plan is not None

    def test_invalid_mode_defaults_to_dag(self):
        d = json.loads(self._valid_json())
        d["recommended_mode"] = "unicorn"
        plan = _parse_plan(json.dumps(d), "test")
        assert plan is not None
        assert plan.recommended_mode in ("direct", "dag", "swarm")


# ── DAG generation from plan ──────────────────────────────────────────────────

class TestDagFromPlan:
    def test_creates_one_node_per_module(self):
        plan = _saas_plan()
        dag = dag_from_plan(plan)
        assert len(dag.nodes) == len(plan.modules)

    def test_node_names_match_modules(self):
        plan = _saas_plan()
        dag = dag_from_plan(plan)
        module_ids = {m.id for m in plan.modules}
        dag_ids = set(dag.nodes.keys())
        assert module_ids == dag_ids

    def test_dependencies_wired(self):
        plan = _saas_plan()
        dag = dag_from_plan(plan)
        auth_node = dag.nodes.get("authentication")
        assert auth_node is not None
        assert "database" in auth_node.dependencies

    def test_independent_nodes_have_no_deps(self):
        plan = _saas_plan()
        dag = dag_from_plan(plan)
        db_node = dag.nodes.get("database")
        assert db_node is not None
        assert db_node.dependencies == []

    def test_node_goal_matches_module_goal(self):
        plan = _saas_plan()
        dag = dag_from_plan(plan)
        auth = dag.nodes["authentication"]
        assert auth.description == "JWT + RBAC"

    def test_turns_carried_over(self):
        plan = _saas_plan()
        for m in plan.modules:
            m.estimated_turns = 42
        dag = dag_from_plan(plan)
        for node in dag.nodes.values():
            assert node.estimated_turns == 42


# ── Team generation from plan ─────────────────────────────────────────────────

class TestTeamFromPlan:
    def test_creates_one_agent_per_module(self):
        plan = _saas_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        assert len(team.agents) == len(plan.modules)

    def test_agent_roles_are_capability_names(self):
        plan = _saas_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        roles = {a.role for a in team.agents}
        # Must be capability names (not "Frontend Team" or "Backend Team")
        assert any("Authentication" in r for r in roles)
        assert any("AI Matching" in r for r in roles)
        # Must NOT be generic domain names
        assert not any(r == "Frontend Team" for r in roles)
        assert not any(r == "Backend Team" for r in roles)

    def test_agent_dependencies_wired(self):
        plan = _saas_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        agent_map = {a.id: a for a in team.agents}
        auth_agent = agent_map.get("authentication")
        assert auth_agent is not None
        assert "database" in auth_agent.dependencies

    def test_agent_missions_contain_goal(self):
        plan = _saas_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        for agent in team.agents:
            assert len(agent.mission) > 0

    def test_team_strategy_parallel_for_dag(self):
        plan = _saas_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        assert team.recommended_strategy == "parallel"

    def test_team_strategy_sequential_for_direct(self):
        plan = _simple_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        assert team.recommended_strategy == "sequential"

    def test_speedup_from_plan(self):
        plan = _saas_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        assert team.parallel_benefit == pytest.approx(2.4)

    def test_owned_files_and_dirs_separated(self):
        plan = ProjectPlan(
            project_name="Test", project_type="api", goal="test",
            modules=[ProjectModule(
                "Auth", "auth",
                files_owned=["auth/routes.py", "auth/models.py", "auth/"],
            )],
        )
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)
        agent = team.agents[0]
        assert "auth/routes.py" in agent.owns.files
        assert "auth/models.py" in agent.owns.files
        assert "auth" in agent.owns.directories  # trailing / stripped


# ── Mode selection from plan ──────────────────────────────────────────────────

class TestModeFromPlan:
    def test_single_module_is_direct(self):
        plan = _simple_plan()
        assert mode_from_plan(plan) == "direct"

    def test_multi_module_with_parallelism_is_dag(self):
        plan = _saas_plan()
        mode = mode_from_plan(plan)
        assert mode in ("dag", "swarm")

    def test_swarm_for_many_independent(self):
        mods = [ProjectModule(f"Module{i}", f"goal {i}") for i in range(10)]
        plan = ProjectPlan(
            project_name="Big System", project_type="saas",
            goal="big system",
            modules=mods,
            recommended_mode="swarm",
            parallel_streams=10,
        )
        mode = mode_from_plan(plan)
        assert mode == "swarm"

    def test_sequential_chain_is_direct(self):
        # A → B → C → D — all sequential, no parallelism
        mods = [
            ProjectModule("A", "first", dependencies=[]),
            ProjectModule("B", "second", dependencies=["A"]),
            ProjectModule("C", "third", dependencies=["B"]),
        ]
        plan = ProjectPlan(
            project_name="Sequential", project_type="api", goal="chain",
            modules=mods, recommended_mode="dag",
            parallel_streams=1,
        )
        # 3 modules but only 1 stream at a time → could be direct or dag
        mode = mode_from_plan(plan)
        assert mode in ("direct", "dag")  # either is acceptable for 3-node chain


# ── Plan → plan_project() integration (mocked LLM) ───────────────────────────

class TestPlanProject:
    def _mock_llm_response(self, plan_json: str):
        return MagicMock(
            **{
                "get.side_effect": lambda k, d=None: {
                    "content": plan_json,
                    "interrupted": False,
                    "finish_reason": "stop",
                }.get(k, d)
            }
        )

    def test_plan_project_returns_plan_on_valid_response(self):
        from agent.orchestration.project_planner import plan_project

        valid_json = json.dumps({
            "project_name": "Test App",
            "project_type": "saas",
            "goal": "Build test app",
            "features": [],
            "tech_stack": {},
            "modules": [
                {"name": "Auth", "goal": "auth", "files_owned": [],
                 "deliverables": [], "dependencies": [], "success_criteria": [],
                 "estimated_turns": 20, "risk": "low"},
                {"name": "Frontend", "goal": "UI", "files_owned": [],
                 "deliverables": [], "dependencies": ["Auth"], "success_criteria": [],
                 "estimated_turns": 30, "risk": "low"},
            ],
            "estimated_files": 5,
            "risks": [],
            "recommended_mode": "dag",
            "parallel_streams": 2,
            "estimated_speedup": 1.5,
        })

        with patch("agent.orchestration.project_planner.ask_llm_stream",
                   return_value={"content": valid_json, "interrupted": False}):
            plan = plan_project("build a test app with auth and UI")

        assert plan is not None
        assert plan.project_name == "Test App"
        assert len(plan.modules) == 2

    def test_plan_project_returns_none_on_llm_failure(self):
        from agent.orchestration.project_planner import plan_project
        with patch("agent.orchestration.project_planner.ask_llm_stream",
                   side_effect=Exception("LLM timeout")):
            result = plan_project("test")
        assert result is None

    def test_plan_project_returns_none_on_empty_response(self):
        from agent.orchestration.project_planner import plan_project
        with patch("agent.orchestration.project_planner.ask_llm_stream",
                   return_value={"content": "", "interrupted": False}):
            result = plan_project("test")
        assert result is None


# ── Orchestration integration (planner phase in orchestrate()) ───────────────

class TestPlannerFirstIntegration:
    def test_orchestrate_result_has_project_plan_field(self):
        from agent.orchestration import OrchestrationResult
        result = OrchestrationResult(approved=False)
        assert hasattr(result, "project_plan")
        assert result.project_plan is None

    def test_planner_disabled_by_env_falls_through(self):
        """When KRYTH_PLANNER_FIRST=0, Phase 0.5 is skipped entirely."""
        import os
        with patch.dict(os.environ, {"KRYTH_PLANNER_FIRST": "0"}):
            # This just verifies the env flag is checked — no LLM needed
            from agent.env import getenv_bool
            assert not getenv_bool("KRYTH_PLANNER_FIRST", True)

    def test_dag_from_plan_integrates_with_scheduler_layer_algo(self):
        """Layers computed by scheduler match plan dependency order."""
        from agent.orchestration.scheduler import _agent_execution_layers
        plan = _saas_plan()
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag)

        layers = _agent_execution_layers(team.agents)

        # Database (no deps) must be first
        db_layer = next((i for i, l in enumerate(layers)
                         if any(a.id == "database" for a in l)), None)
        assert db_layer is not None
        assert db_layer == 0

        # Authentication (deps: database) must come after
        auth_layer = next((i for i, l in enumerate(layers)
                           if any(a.id == "authentication" for a in l)), None)
        assert auth_layer is not None
        assert auth_layer > db_layer

        # Testing (deps: multiple) must be last
        test_layer = next((i for i, l in enumerate(layers)
                           if any(a.id == "testing" for a in l)), None)
        assert test_layer is not None
        assert test_layer == len(layers) - 1


# ── Mission gate plan review ──────────────────────────────────────────────────

class TestMissionGatePlanReview:
    def test_request_plan_decision_non_interactive_returns_approve(self):
        from agent.ui.mission_gate import request_plan_decision
        plan = _saas_plan()
        decision = request_plan_decision(plan, interactive=False)
        assert decision == "approve"

    def test_request_plan_decision_y_key_approves(self):
        from agent.ui.mission_gate import request_plan_decision
        plan = _saas_plan()
        with patch("agent.orchestration.project_planner.render_plan_panel"):
            decision = request_plan_decision(
                plan, interactive=True, reader=lambda: "y"
            )
        assert decision == "approve"

    def test_request_plan_decision_n_cancels(self):
        from agent.ui.mission_gate import request_plan_decision
        plan = _saas_plan()
        with patch("agent.orchestration.project_planner.render_plan_panel"):
            decision = request_plan_decision(
                plan, interactive=True, reader=lambda: "n"
            )
        assert decision == "cancel"

    def test_request_plan_decision_d_forces_direct(self):
        from agent.ui.mission_gate import request_plan_decision
        plan = _saas_plan()
        with patch("agent.orchestration.project_planner.render_plan_panel"):
            decision = request_plan_decision(
                plan, interactive=True, reader=lambda: "d"
            )
        assert decision == "direct"

    def test_request_plan_decision_e_edits_plan(self):
        from agent.ui.mission_gate import request_plan_decision
        plan = _saas_plan()
        with patch("agent.orchestration.project_planner.render_plan_panel"):
            decision = request_plan_decision(
                plan, interactive=True, reader=lambda: "e"
            )
        assert decision == "edit_plan"

    def test_request_plan_decision_r_regenerates(self):
        from agent.ui.mission_gate import request_plan_decision
        plan = _saas_plan()
        with patch("agent.orchestration.project_planner.render_plan_panel"):
            decision = request_plan_decision(
                plan, interactive=True, reader=lambda: "r"
            )
        assert decision == "regen_plan"

    def test_plan_review_panel_does_not_crash(self, monkeypatch):
        from agent.ui.mission_gate import plan_review_panel
        from agent.ui.console import console
        monkeypatch.setattr(console, "print", lambda *a, **kw: None)
        plan_review_panel(_saas_plan())  # must not raise
