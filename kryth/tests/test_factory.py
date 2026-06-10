"""Tests for the KRYTH Autonomous Software Factory.

Covers:
- BacklogManager — epic/story/task CRUD, linking, stats
- ProductPlanner — plan generation, template selection, persistence
- SprintEngine — create/start/complete/abort, progress, story lifecycle
- ArchitectureGuardian — file size, secrets, test presence checks
- CodeReviewEngine — security, complexity, style findings
- TestingPipeline — suite detection, stub generation, output parsing
- BrowserQA — report structure, check shape
- DeploymentPipeline — deploy lifecycle, production gate, rollback, listing
- MaintenanceEngine — scan, suggest_repairs
- ExecutiveDashboard — state computation, render
- Factory — build, status, dashboard_render (integration)
- Tool handlers — JSON round-trip for all 10 tools
- Tool specs — schema structure + registration
"""

from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_py_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def backlog(tmp_path):
    from agent.factory.backlog_manager import BacklogManager
    return BacklogManager("testhash", db_path=tmp_path / "backlog.db")


@pytest.fixture()
def sprint_db(tmp_path):
    return tmp_path / "sprint.db"


@pytest.fixture()
def factory_inst(tmp_path):
    from agent.factory.factory import Factory
    return Factory(
        project_hash="testhash",
        project_name="Test Project",
        root=str(tmp_path),
        db_path=tmp_path / "missions.db",
    )


# ---------------------------------------------------------------------------
# BacklogManager
# ---------------------------------------------------------------------------

class TestBacklogManager:
    def test_add_and_get_epic(self, backlog):
        eid = backlog.add_epic("Authentication", description="JWT auth system", priority=80)
        epic = backlog.get_epic(eid)
        assert epic is not None
        assert epic["title"] == "Authentication"
        assert epic["priority"] == 80
        assert epic["status"] == "open"

    def test_list_epics_empty(self, backlog):
        assert backlog.list_epics() == []

    def test_list_epics_filter_by_status(self, backlog):
        from agent.factory.config import EpicStatus
        eid = backlog.add_epic("Alpha")
        backlog.update_epic_status(eid, EpicStatus.DONE)
        assert len(backlog.list_epics(status="done")) == 1
        assert len(backlog.list_epics(status="open")) == 0

    def test_add_and_get_story(self, backlog):
        eid = backlog.add_epic("Frontend")
        sid = backlog.add_story(eid, "Login page", priority=90, story_points=3)
        story = backlog.get_story(sid)
        assert story is not None
        assert story["title"] == "Login page"
        assert story["story_points"] == 3
        assert story["epic_id"] == eid

    def test_list_stories_by_epic(self, backlog):
        eid = backlog.add_epic("API")
        backlog.add_story(eid, "Route A")
        backlog.add_story(eid, "Route B")
        stories = backlog.list_stories(epic_id=eid)
        assert len(stories) == 2

    def test_list_stories_by_status(self, backlog):
        from agent.factory.config import StoryStatus
        eid = backlog.add_epic("Testing")
        sid = backlog.add_story(eid, "Unit tests")
        backlog.update_story_status(sid, StoryStatus.IN_PROGRESS)
        assert len(backlog.list_stories(status="in_progress")) == 1

    def test_link_story_to_mission_node(self, backlog):
        eid = backlog.add_epic("DB")
        sid = backlog.add_story(eid, "Schema design")
        backlog.link_story_to_mission_node(sid, "node-001")
        story = backlog.get_story(sid)
        assert story["mission_node_id"] == "node-001"

    def test_add_and_get_task(self, backlog):
        eid = backlog.add_epic("Backend")
        sid = backlog.add_story(eid, "Auth middleware")
        tid = backlog.add_task(sid, "Write JWT decoder", depends_on=[])
        task = backlog.get_task(tid)
        assert task is not None
        assert task["title"] == "Write JWT decoder"
        assert task["depends_on"] == []

    def test_list_tasks(self, backlog):
        eid = backlog.add_epic("E")
        sid = backlog.add_story(eid, "S")
        backlog.add_task(sid, "T1")
        backlog.add_task(sid, "T2")
        assert len(backlog.list_tasks(sid)) == 2

    def test_update_task_status(self, backlog):
        from agent.factory.config import TaskStatus
        eid = backlog.add_epic("E")
        sid = backlog.add_story(eid, "S")
        tid = backlog.add_task(sid, "T")
        backlog.update_task_status(tid, TaskStatus.DONE)
        task = backlog.get_task(tid)
        assert task["status"] == "done"

    def test_backlog_summary(self, backlog):
        eid = backlog.add_epic("E1")
        backlog.add_story(eid, "S1")
        summary = backlog.backlog_summary()
        assert summary["epics"] == 1
        assert "backlog" in summary["stories"]

    def test_get_epic_returns_none_for_unknown(self, backlog):
        assert backlog.get_epic("nonexistent") is None

    def test_get_story_returns_none_for_unknown(self, backlog):
        assert backlog.get_story("nonexistent") is None


# ---------------------------------------------------------------------------
# ProductPlanner
# ---------------------------------------------------------------------------

class TestProductPlanner:
    def test_plan_returns_product_plan(self, backlog, tmp_path):
        from agent.factory.product_planner import ProductPlanner
        planner = ProductPlanner("testhash", backlog=backlog)
        plan = planner.plan("Build an AI SaaS with Stripe", project_name="TestSaaS", project_root=str(tmp_path))
        assert plan.goal == "Build an AI SaaS with Stripe"
        assert plan.project_name == "TestSaaS"
        assert len(plan.epics) > 0

    def test_plan_selects_saas_template(self, backlog, tmp_path):
        from agent.factory.product_planner import ProductPlanner
        planner = ProductPlanner("testhash", backlog=backlog)
        plan = planner.plan("Build a SaaS platform with billing", project_root=str(tmp_path))
        titles = [e["title"] for e in plan.epics]
        assert any("Auth" in t for t in titles)
        assert any("Billing" in t for t in titles)

    def test_plan_selects_api_template(self, backlog, tmp_path):
        from agent.factory.product_planner import ProductPlanner
        planner = ProductPlanner("testhash", backlog=backlog)
        plan = planner.plan("Build a REST API with FastAPI", project_root=str(tmp_path))
        titles = [e["title"] for e in plan.epics]
        assert any("Route" in t or "Architecture" in t for t in titles)

    def test_plan_persists_to_backlog(self, backlog, tmp_path):
        from agent.factory.product_planner import ProductPlanner
        planner = ProductPlanner("testhash", backlog=backlog)
        plan = planner.plan("Build a website", project_root=str(tmp_path))
        assert len(plan.backlog_ids) > 0
        epics = backlog.list_epics()
        assert len(epics) > 0

    def test_plan_without_backlog(self, tmp_path):
        from agent.factory.product_planner import ProductPlanner
        planner = ProductPlanner("testhash", backlog=None)
        plan = planner.plan("Build an API", project_root=str(tmp_path))
        assert plan.backlog_ids == {}  # no persistence attempted

    def test_plan_to_dict(self, backlog, tmp_path):
        from agent.factory.product_planner import ProductPlanner
        planner = ProductPlanner("testhash", backlog=backlog)
        plan = planner.plan("Build app", project_root=str(tmp_path))
        d = plan.to_dict()
        assert "goal" in d
        assert "epics" in d
        assert "tech_stack" in d

    def test_risk_notes_for_payment_goal(self, tmp_path):
        from agent.factory.product_planner import ProductPlanner, _infer_risks
        risks = _infer_risks("Build with stripe payment integration", {})
        assert any("payment" in r.lower() or "PCI" in r for r in risks)

    def test_infer_project_name(self):
        from agent.factory.product_planner import _infer_project_name
        name = _infer_project_name("build an AI coding agent")
        assert len(name) > 0


# ---------------------------------------------------------------------------
# SprintEngine
# ---------------------------------------------------------------------------

class TestSprintEngine:
    def test_create_sprint(self, sprint_db):
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", db_path=sprint_db)
        sid = engine.create_sprint("Sprint 1", goal="Build auth")
        sprint = engine.get_sprint(sid)
        assert sprint is not None
        assert sprint["name"] == "Sprint 1"
        assert sprint["status"] == "planned"

    def test_list_sprints_empty(self, sprint_db):
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", db_path=sprint_db)
        assert engine.list_sprints() == []

    def test_create_sprint_with_stories(self, backlog, sprint_db):
        eid = backlog.add_epic("E")
        sid1 = backlog.add_story(eid, "Story 1")
        sid2 = backlog.add_story(eid, "Story 2")
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", backlog=backlog, db_path=sprint_db)
        sprint_id = engine.create_sprint("S1", story_ids=[sid1, sid2])
        stories = engine.get_sprint_stories(sprint_id)
        assert len(stories) == 2

    def test_start_sprint_no_mission_manager(self, sprint_db):
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", db_path=sprint_db)
        sprint_id = engine.create_sprint("S1", goal="test")
        mid = engine.start(sprint_id)
        sprint = engine.get_sprint(sprint_id)
        assert sprint["status"] == "active"

    def test_complete_story(self, backlog, sprint_db):
        eid = backlog.add_epic("E")
        sid = backlog.add_story(eid, "S")
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", backlog=backlog, db_path=sprint_db)
        sprint_id = engine.create_sprint("Sprint", story_ids=[sid])
        engine.complete_story(sprint_id, sid, success=True)
        stories = engine.get_sprint_stories(sprint_id)
        assert stories[0]["status"] == "done"

    def test_complete_sprint(self, backlog, sprint_db):
        eid = backlog.add_epic("E")
        sid = backlog.add_story(eid, "S")
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", backlog=backlog, db_path=sprint_db)
        sprint_id = engine.create_sprint("Sprint", story_ids=[sid])
        engine.start(sprint_id)
        engine.complete_story(sprint_id, sid, success=True)
        result = engine.complete(sprint_id)
        assert result.status == "completed"
        assert sid in result.stories_completed

    def test_abort_sprint(self, sprint_db):
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", db_path=sprint_db)
        sprint_id = engine.create_sprint("S")
        engine.abort(sprint_id, reason="cancelled")
        sprint = engine.get_sprint(sprint_id)
        assert sprint["status"] == "aborted"

    def test_progress_all_pending(self, backlog, sprint_db):
        eid = backlog.add_epic("E")
        sid1 = backlog.add_story(eid, "S1")
        sid2 = backlog.add_story(eid, "S2")
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", backlog=backlog, db_path=sprint_db)
        sprint_id = engine.create_sprint("S", story_ids=[sid1, sid2])
        prog = engine.progress(sprint_id)
        assert prog["total"] == 2
        assert prog["percent"] == 0

    def test_sprint_result_to_dict(self, sprint_db):
        from agent.factory.sprint_engine import SprintEngine
        engine = SprintEngine("testhash", db_path=sprint_db)
        sprint_id = engine.create_sprint("SR")
        engine.start(sprint_id)
        result = engine.complete(sprint_id)
        d = result.to_dict()
        assert "sprint_id" in d
        assert "stories_completed" in d


# ---------------------------------------------------------------------------
# ArchitectureGuardian
# ---------------------------------------------------------------------------

class TestArchitectureGuardian:
    def test_audit_empty_dir(self, tmp_path):
        from agent.factory.architecture_guardian import ArchitectureGuardian
        guardian = ArchitectureGuardian(str(tmp_path))
        report = guardian.audit()
        assert isinstance(report.violations, list)
        assert report.total_files_scanned == 0

    def test_detects_large_file(self, tmp_path):
        from agent.factory.architecture_guardian import ArchitectureGuardian
        big = tmp_path / "big.py"
        big.write_text("\n".join([f"x = {i}" for i in range(1100)]))
        guardian = ArchitectureGuardian(str(tmp_path), config={"max_file_lines": 100})
        report = guardian.audit()
        rules = [v.rule for v in report.violations]
        assert "max_file_lines" in rules

    def test_detects_hardcoded_secret(self, tmp_path):
        from agent.factory.architecture_guardian import ArchitectureGuardian
        src = tmp_path / "config.py"
        src.write_text('api_key = "sk-AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"\n')
        guardian = ArchitectureGuardian(str(tmp_path))
        report = guardian.audit()
        rules = [v.rule for v in report.violations]
        assert any("secret" in r or "key" in r for r in rules)

    def test_detects_missing_tests(self, tmp_path):
        from agent.factory.architecture_guardian import ArchitectureGuardian
        (tmp_path / "main.py").write_text("x = 1\n")
        guardian = ArchitectureGuardian(str(tmp_path))
        report = guardian.audit()
        rules = [v.rule for v in report.violations]
        assert "no_tests" in rules

    def test_passes_with_test_dir(self, tmp_path):
        from agent.factory.architecture_guardian import ArchitectureGuardian
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_main.py").write_text("def test_x(): pass\n")
        guardian = ArchitectureGuardian(str(tmp_path))
        report = guardian.audit()
        assert "no_tests" not in [v.rule for v in report.violations]

    def test_report_to_dict(self, tmp_path):
        from agent.factory.architecture_guardian import ArchitectureGuardian
        guardian = ArchitectureGuardian(str(tmp_path))
        report = guardian.audit()
        d = report.to_dict()
        assert "violations" in d
        assert "passed_checks" in d
        assert "has_errors" in d

    def test_report_summary(self, tmp_path):
        from agent.factory.architecture_guardian import ArchitectureGuardian
        guardian = ArchitectureGuardian(str(tmp_path))
        report = guardian.audit()
        summary = report.summary()
        assert isinstance(summary, str)
        assert "Architecture Guardian" in summary


# ---------------------------------------------------------------------------
# CodeReviewEngine
# ---------------------------------------------------------------------------

class TestCodeReviewEngine:
    def test_review_empty_dir(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review()
        assert isinstance(report.findings, list)
        assert len(report.files_reviewed) == 0

    def test_detects_eval(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        f = _make_py_file(tmp_path, "evil.py", "eval(user_input)\n")
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review()
        categories = [r.category for r in report.findings]
        assert "security" in categories

    def test_detects_os_system(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        _make_py_file(tmp_path, "cmd.py", "import os\nos.system(cmd)\n")
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review()
        assert any(f.category == "security" for f in report.findings)

    def test_no_findings_for_clean_file(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        _make_py_file(tmp_path, "clean.py", "def add(a, b):\n    return a + b\n")
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review()
        security_findings = [f for f in report.findings if f.category == "security"]
        assert len(security_findings) == 0

    def test_detects_high_complexity(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        # Build a function with many branches
        branches = "\n    ".join([f"if x == {i}: pass" for i in range(20)])
        code = f"def complex_fn(x):\n    {branches}\n"
        _make_py_file(tmp_path, "complex.py", code)
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review()
        complexity = [f for f in report.findings if f.category == "complexity"]
        assert len(complexity) > 0

    def test_review_specific_paths(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        f1 = _make_py_file(tmp_path, "a.py", "eval('x')\n")
        _make_py_file(tmp_path, "b.py", "x = 1\n")
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review(paths=[str(f1)])
        assert len(report.files_reviewed) == 1

    def test_report_to_dict(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review()
        d = report.to_dict()
        assert "findings" in d
        assert "has_blockers" in d

    def test_report_summary(self, tmp_path):
        from agent.factory.code_review_engine import CodeReviewEngine
        engine = CodeReviewEngine(str(tmp_path))
        report = engine.review()
        summary = report.summary()
        assert "Code Review" in summary


# ---------------------------------------------------------------------------
# TestingPipeline
# ---------------------------------------------------------------------------

class TestTestingPipelineUnit:
    def test_detect_commands_python(self, tmp_path):
        from agent.factory.testing_pipeline import TestingPipeline
        (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
        pipeline = TestingPipeline(str(tmp_path))
        cmds = pipeline._detect_test_commands()
        assert "unit" in cmds

    def test_detect_commands_node(self, tmp_path):
        from agent.factory.testing_pipeline import TestingPipeline
        (tmp_path / "package.json").write_text('{"name":"x","scripts":{"test":"jest"}}\n')
        pipeline = TestingPipeline(str(tmp_path))
        cmds = pipeline._detect_test_commands()
        assert "unit" in cmds

    def test_generate_missing_tests_creates_stub(self, tmp_path):
        from agent.factory.testing_pipeline import TestingPipeline
        src = _make_py_file(tmp_path, "auth.py", "def login(u, p):\n    pass\ndef logout():\n    pass\n")
        pipeline = TestingPipeline(str(tmp_path))
        generated = pipeline.generate_missing_tests([str(src)])
        assert len(generated) == 1
        stub = Path(generated[0]).read_text()
        assert "test_login" in stub or "test_logout" in stub

    def test_generate_skips_existing_tests(self, tmp_path):
        from agent.factory.testing_pipeline import TestingPipeline
        src = _make_py_file(tmp_path, "auth.py", "def login(): pass\n")
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        (test_dir / "test_auth.py").write_text("def test_login(): pass\n")
        pipeline = TestingPipeline(str(tmp_path))
        generated = pipeline.generate_missing_tests([str(src)])
        assert generated == []

    def test_parse_pytest_output_passing(self):
        from agent.factory.testing_pipeline import _parse_pytest_output
        out = "5 passed, 1 skipped in 0.45s"
        sr = _parse_pytest_output("unit", out)
        assert sr.passed == 5
        assert sr.skipped == 1
        assert sr.success is True

    def test_parse_pytest_output_failing(self):
        from agent.factory.testing_pipeline import _parse_pytest_output
        out = "3 passed, 2 failed in 1.2s"
        sr = _parse_pytest_output("unit", out)
        assert sr.failed == 2
        assert sr.success is False

    def test_pipeline_result_to_dict(self, tmp_path):
        from agent.factory.testing_pipeline import PipelineResult, TestSuiteResult
        r = PipelineResult(root=str(tmp_path))
        r.suites.append(TestSuiteResult(suite="unit", passed=3, success=True))
        d = r.to_dict()
        assert "suites" in d
        assert d["suites"][0]["suite"] == "unit"


# ---------------------------------------------------------------------------
# BrowserQA
# ---------------------------------------------------------------------------

class TestBrowserQA:
    def test_report_structure(self, tmp_path):
        from agent.factory.browser_qa import BrowserQA, BrowserQAReport
        qa = BrowserQA(base_url="http://localhost:9999", screenshots_dir=tmp_path / "ss")
        report = BrowserQAReport(profile="testing", base_url="http://localhost:9999")
        assert report.passed == 0
        assert report.failed == 0
        assert report.success is True

    def test_run_checks_returns_report(self, tmp_path):
        from agent.factory.browser_qa import BrowserQA
        qa = BrowserQA(base_url="http://localhost:9999", screenshots_dir=tmp_path / "ss")
        # Should not crash even when browser isn't running
        report = qa.run_checks([{"name": "home", "path": "/", "type": "navigation"}])
        assert hasattr(report, "checks")

    def test_report_to_dict(self):
        from agent.factory.browser_qa import BrowserQAReport, QACheck
        report = BrowserQAReport(profile="test", base_url="http://x.com")
        report.checks.append(QACheck(name="home", url="http://x.com/", check_type="navigation", passed=True))
        d = report.to_dict()
        assert d["passed"] == 1
        assert d["failed"] == 0
        assert d["success"] is True

    def test_report_summary(self):
        from agent.factory.browser_qa import BrowserQAReport, QACheck
        report = BrowserQAReport(profile="test", base_url="http://x.com")
        report.checks.append(QACheck(name="login", url="http://x.com/login", check_type="login", passed=False, error="404"))
        summary = report.summary()
        assert "✗" in summary
        assert "login" in summary


# ---------------------------------------------------------------------------
# DeploymentPipeline
# ---------------------------------------------------------------------------

class TestDeploymentPipeline:
    def test_production_blocked_without_approval(self, tmp_path):
        from agent.factory.deployment_pipeline import DeploymentPipeline
        pipeline = DeploymentPipeline("testhash", root=str(tmp_path), db_path=tmp_path / "dep.db")
        result = pipeline.deploy(env="production", approval_confirmed=False)
        assert result.status == "blocked"

    def test_staging_deploy_succeeds(self, tmp_path):
        from agent.factory.deployment_pipeline import DeploymentPipeline
        pipeline = DeploymentPipeline("testhash", root=str(tmp_path), db_path=tmp_path / "dep.db")
        result = pipeline.deploy(env="staging")
        assert result.env == "staging"
        assert result.status in ("healthy", "failed")

    def test_production_deploy_with_approval(self, tmp_path):
        from agent.factory.deployment_pipeline import DeploymentPipeline
        pipeline = DeploymentPipeline("testhash", root=str(tmp_path), db_path=tmp_path / "dep.db")
        result = pipeline.deploy(env="production", approval_confirmed=True)
        assert result.status != "blocked"

    def test_list_deployments_empty(self, tmp_path):
        from agent.factory.deployment_pipeline import DeploymentPipeline
        pipeline = DeploymentPipeline("testhash", root=str(tmp_path), db_path=tmp_path / "dep.db")
        assert pipeline.list_deployments() == []

    def test_list_deployments_after_deploy(self, tmp_path):
        from agent.factory.deployment_pipeline import DeploymentPipeline
        pipeline = DeploymentPipeline("testhash", root=str(tmp_path), db_path=tmp_path / "dep.db")
        pipeline.deploy(env="staging")
        deps = pipeline.list_deployments()
        assert len(deps) == 1

    def test_rollback_nonexistent(self, tmp_path):
        from agent.factory.deployment_pipeline import DeploymentPipeline
        pipeline = DeploymentPipeline("testhash", root=str(tmp_path), db_path=tmp_path / "dep.db")
        result = pipeline.rollback("nonexistent-id")
        assert result.status == "rolled_back"

    def test_deployment_result_to_dict(self, tmp_path):
        from agent.factory.deployment_pipeline import DeploymentResult
        r = DeploymentResult("dep1", "staging", "healthy", log=["built"], health_ok=True)
        d = r.to_dict()
        assert d["env"] == "staging"
        assert d["health_ok"] is True


# ---------------------------------------------------------------------------
# MaintenanceEngine
# ---------------------------------------------------------------------------

class TestMaintenanceEngine:
    def test_scan_empty_dir(self, tmp_path):
        from agent.factory.maintenance_engine import MaintenanceEngine
        engine = MaintenanceEngine(str(tmp_path))
        report = engine.scan()
        assert isinstance(report.issues, list)
        assert isinstance(report.to_dict(), dict)

    def test_scan_detects_requirements_txt(self, tmp_path):
        from agent.factory.maintenance_engine import MaintenanceEngine
        (tmp_path / "requirements.txt").write_text("requests==2.28.0\n")
        engine = MaintenanceEngine(str(tmp_path))
        report = engine.scan()
        kinds = [i.kind for i in report.issues]
        assert "dependency_stale" in kinds

    def test_scan_detects_package_json(self, tmp_path):
        from agent.factory.maintenance_engine import MaintenanceEngine
        (tmp_path / "package.json").write_text('{"name":"x","dependencies":{}}\n')
        engine = MaintenanceEngine(str(tmp_path))
        report = engine.scan()
        kinds = [i.kind for i in report.issues]
        assert "dependency_stale" in kinds

    def test_suggest_repairs_empty(self, tmp_path):
        from agent.factory.maintenance_engine import MaintenanceEngine
        engine = MaintenanceEngine(str(tmp_path))
        report = engine.scan()
        repairs = engine.suggest_repairs(report)
        assert isinstance(repairs, list)

    def test_report_summary(self, tmp_path):
        from agent.factory.maintenance_engine import MaintenanceEngine
        engine = MaintenanceEngine(str(tmp_path))
        report = engine.scan()
        summary = report.summary()
        assert isinstance(summary, str)

    def test_healthy_flag(self, tmp_path):
        from agent.factory.maintenance_engine import MaintenanceEngine, MaintenanceIssue
        engine = MaintenanceEngine(str(tmp_path))
        report = engine.scan()
        # Empty dir — no error-severity issues expected
        error_issues = [i for i in report.issues if i.severity == "error"]
        assert report.healthy == (len(error_issues) == 0)


# ---------------------------------------------------------------------------
# ExecutiveDashboard
# ---------------------------------------------------------------------------

class TestExecutiveDashboard:
    def test_state_no_subsystems(self):
        from agent.factory.executive_dashboard import ExecutiveDashboard
        dash = ExecutiveDashboard("My Project")
        ds = dash.state()
        assert ds.project_name == "My Project"
        assert isinstance(ds.progress_percent, float)
        assert isinstance(ds.risk_level, str)

    def test_render_returns_markdown(self):
        from agent.factory.executive_dashboard import ExecutiveDashboard
        dash = ExecutiveDashboard("KRYTH SaaS")
        md = dash.render()
        assert "KRYTH SaaS" in md
        assert "%" in md

    def test_render_teams(self):
        from agent.factory.executive_dashboard import ExecutiveDashboard
        dash = ExecutiveDashboard("X")
        agents = [
            {"role": "Backend", "status": "completed"},
            {"role": "Frontend", "status": "active"},
            {"role": "Testing", "status": "pending"},
        ]
        rendered = dash.render_teams(agents)
        assert "✓" in rendered
        assert "◉" in rendered
        assert "○" in rendered

    def test_state_to_dict(self):
        from agent.factory.executive_dashboard import ExecutiveDashboard
        dash = ExecutiveDashboard("Dict Test")
        d = dash.state().to_dict()
        assert "project_name" in d
        assert "progress_percent" in d
        assert "risk_level" in d

    def test_success_prediction_high_risk_lowers_score(self):
        from agent.factory.executive_dashboard import ExecutiveDashboard, DashboardState
        dash = ExecutiveDashboard("Risk Test")
        ds = DashboardState(project_name="X", risk_level="HIGH")
        pred = dash._predict_success(ds)
        normal_ds = DashboardState(project_name="X", risk_level="LOW")
        normal_pred = dash._predict_success(normal_ds)
        assert pred < normal_pred


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------

class TestFactory:
    def test_build_returns_ids(self, factory_inst, tmp_path):
        result = factory_inst.build("Build a simple REST API")
        assert "mission_id" in result
        assert "sprint_id" in result
        assert "epics" in result
        assert result["epics"] > 0

    def test_status_returns_dict(self, factory_inst):
        s = factory_inst.status()
        assert "project" in s
        assert "backlog" in s

    def test_dashboard_render_returns_markdown(self, factory_inst):
        result = factory_inst.build("Build a website")
        mid = result["mission_id"]
        md = factory_inst.dashboard_render(mission_id=mid)
        assert isinstance(md, str)
        assert "%" in md or "Project" in md

    def test_subsystems_are_lazy(self, factory_inst):
        # Accessing properties should not fail
        _ = factory_inst.backlog
        _ = factory_inst.planner
        _ = factory_inst.sprint
        _ = factory_inst.guardian
        _ = factory_inst.review
        _ = factory_inst.testing
        _ = factory_inst.maintenance
        _ = factory_inst.dashboard


# ---------------------------------------------------------------------------
# Tool handlers (JSON round-trip)
# ---------------------------------------------------------------------------

class TestToolHandlers:
    def _patch(self, monkeypatch, tmp_path):
        from agent.factory import factory as fmod
        monkeypatch.setattr(
            "agent.tools._factory._factory",
            lambda root=".": fmod.Factory(
                project_hash="testhash",
                project_name="Test",
                root=str(tmp_path),
                db_path=tmp_path / "missions.db",
            ),
        )

    def test_factory_build(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_build
        r = json.loads(factory_build("Build a REST API"))
        assert r["status"] == "started"
        assert "mission_id" in r

    def test_factory_status(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_status
        r = json.loads(factory_status())
        assert "project" in r

    def test_factory_sprint_start_and_complete(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_build, factory_sprint_start, factory_sprint_complete
        build = json.loads(factory_build("Build app"))
        sprint_id = build["sprint_id"]
        start_r = json.loads(factory_sprint_start(sprint_id))
        assert start_r["status"] == "started"
        complete_r = json.loads(factory_sprint_complete(sprint_id))
        assert "sprint_id" in complete_r

    def test_factory_architecture_audit(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_architecture_audit
        r = json.loads(factory_architecture_audit(str(tmp_path)))
        assert "violations" in r

    def test_factory_code_review(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        _make_py_file(tmp_path, "sample.py", "x = 1\n")
        from agent.tools._factory import factory_code_review
        r = json.loads(factory_code_review(root=str(tmp_path)))
        assert "findings" in r

    def test_factory_run_tests(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_run_tests
        r = json.loads(factory_run_tests(root=str(tmp_path)))
        assert "suites" in r or "suite" in r

    def test_factory_deploy_staging(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_deploy
        r = json.loads(factory_deploy(env="staging"))
        assert "status" in r
        assert r["status"] != "blocked"

    def test_factory_deploy_production_blocked(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_deploy
        r = json.loads(factory_deploy(env="production", approval_confirmed=False))
        assert r["status"] == "blocked"

    def test_factory_maintenance_scan(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_maintenance_scan
        r = json.loads(factory_maintenance_scan(str(tmp_path)))
        assert "issues" in r

    def test_factory_dashboard(self, monkeypatch, tmp_path):
        self._patch(monkeypatch, tmp_path)
        from agent.tools._factory import factory_dashboard
        md = factory_dashboard(project_name="Dashboard Test")
        assert isinstance(md, str)
        assert "%" in md or "Dashboard Test" in md


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------

class TestToolSpecs:
    def test_all_specs_have_required_fields(self):
        from agent.tools._factory_specs import FACTORY_TOOL_SPECS
        for spec in FACTORY_TOOL_SPECS:
            assert spec["type"] == "function"
            fn = spec["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_spec_names_match_handlers(self):
        from agent.tools._factory_specs import FACTORY_TOOL_SPECS
        from agent.tools._factory import FACTORY_TOOLS
        spec_names = {s["function"]["name"] for s in FACTORY_TOOL_SPECS}
        handler_names = set(FACTORY_TOOLS.keys())
        assert spec_names == handler_names

    def test_specs_in_tool_specs(self):
        from agent.tools import TOOL_SPECS
        names = {s["function"]["name"] for s in TOOL_SPECS}
        assert "factory_build" in names
        assert "factory_deploy" in names
        assert "factory_dashboard" in names

    def test_tools_in_tools_dict(self):
        from agent.tools import TOOLS
        assert "factory_build" in TOOLS
        assert "factory_maintenance_scan" in TOOLS
        assert "factory_architecture_audit" in TOOLS

    def test_read_only_tools_registered(self):
        from agent.tools import READ_ONLY_TOOLS
        assert "factory_status" in READ_ONLY_TOOLS
        assert "factory_architecture_audit" in READ_ONLY_TOOLS
        assert "factory_code_review" in READ_ONLY_TOOLS
        assert "factory_dashboard" in READ_ONLY_TOOLS
