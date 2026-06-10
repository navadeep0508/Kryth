"""Tool implementations for the Autonomous Software Factory."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _factory(root: str = ".") -> "Factory":  # noqa: F821
    from agent.factory.factory import Factory
    return Factory(root=root)


# ------------------------------------------------------------------
# Tool handlers
# ------------------------------------------------------------------

def factory_build(goal: str, project_name: str = "") -> str:
    """Start a full product build from a high-level goal."""
    try:
        f = _factory()
        if project_name:
            f.project_name = project_name
        result = f.build(goal)
        return json.dumps({"status": "started", **result})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_status(mission_id: str = "") -> str:
    """Get the current factory status."""
    try:
        f = _factory()
        return json.dumps(f.status(mission_id=mission_id))
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_sprint_start(sprint_id: str) -> str:
    """Start a sprint."""
    try:
        f = _factory()
        mission_id = f.sprint.start(sprint_id)
        sprint = f.sprint.get_sprint(sprint_id)
        return json.dumps({
            "status": "started",
            "sprint_id": sprint_id,
            "sprint_name": sprint["name"] if sprint else "",
            "mission_id": mission_id,
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_sprint_complete(sprint_id: str) -> str:
    """Complete a sprint and return a summary."""
    try:
        f = _factory()
        result = f.sprint.complete(sprint_id)
        return json.dumps(result.to_dict())
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_architecture_audit(root: str = ".") -> str:
    """Run an architecture audit."""
    try:
        from agent.factory.architecture_guardian import ArchitectureGuardian
        guardian = ArchitectureGuardian(root or ".")
        report = guardian.audit()
        return json.dumps(report.to_dict())
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_code_review(
    paths: list[str] | None = None,
    root: str = ".",
) -> str:
    """Run automated code review."""
    try:
        from agent.factory.code_review_engine import CodeReviewEngine
        engine = CodeReviewEngine(root or ".")
        report = engine.review(paths=paths or None)
        return json.dumps(report.to_dict())
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_run_tests(suite: str = "", root: str = ".") -> str:
    """Run the project's test suites."""
    try:
        from agent.factory.testing_pipeline import TestingPipeline
        pipeline = TestingPipeline(root or ".")
        if suite:
            result = pipeline.run_suite(suite)
            return json.dumps(result.to_dict())
        else:
            result = pipeline.run_all()
            return json.dumps(result.to_dict())
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_deploy(
    env: str = "staging",
    mission_id: str = "",
    approval_confirmed: bool = False,
) -> str:
    """Run the deployment pipeline."""
    try:
        f = _factory()
        result = f.deployment.deploy(
            env=env,
            mission_id=mission_id,
            approval_confirmed=approval_confirmed,
        )
        return json.dumps(result.to_dict())
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_maintenance_scan(root: str = ".") -> str:
    """Scan a project for health issues."""
    try:
        from agent.factory.maintenance_engine import MaintenanceEngine
        engine = MaintenanceEngine(root or ".")
        report = engine.scan()
        return json.dumps(report.to_dict())
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def factory_dashboard(mission_id: str = "", project_name: str = "") -> str:
    """Render the executive project dashboard."""
    try:
        f = _factory()
        if project_name:
            f.project_name = project_name
        return f.dashboard_render(mission_id=mission_id)
    except Exception as exc:
        return f"[factory_dashboard error] {exc}"


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

FACTORY_TOOLS: dict[str, Any] = {
    "factory_build": factory_build,
    "factory_status": factory_status,
    "factory_sprint_start": factory_sprint_start,
    "factory_sprint_complete": factory_sprint_complete,
    "factory_architecture_audit": factory_architecture_audit,
    "factory_code_review": factory_code_review,
    "factory_run_tests": factory_run_tests,
    "factory_deploy": factory_deploy,
    "factory_maintenance_scan": factory_maintenance_scan,
    "factory_dashboard": factory_dashboard,
}
