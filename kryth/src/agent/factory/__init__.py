"""KRYTH Autonomous Software Factory.

Transforms KRYTH into an autonomous software engineering organization —
plan, build, test, deploy, maintain, and learn from every project.

Quick start::

    from agent.factory import Factory

    f = Factory(project_name="My SaaS", root=".")
    result = f.build("Build an AI SaaS with Stripe billing")
    print(f.dashboard_render(result["mission_id"]))
"""

from agent.factory.factory import Factory
from agent.factory.backlog_manager import BacklogManager
from agent.factory.product_planner import ProductPlanner, ProductPlan
from agent.factory.sprint_engine import SprintEngine, SprintResult
from agent.factory.architecture_guardian import ArchitectureGuardian, GuardianReport, ArchViolation
from agent.factory.code_review_engine import CodeReviewEngine, ReviewReport, ReviewFinding
from agent.factory.testing_pipeline import TestingPipeline, PipelineResult, TestSuiteResult
from agent.factory.browser_qa import BrowserQA, BrowserQAReport, QACheck
from agent.factory.deployment_pipeline import DeploymentPipeline, DeploymentResult
from agent.factory.maintenance_engine import MaintenanceEngine, MaintenanceReport, MaintenanceIssue
from agent.factory.executive_dashboard import ExecutiveDashboard, DashboardState
from agent.factory.config import (
    EpicStatus, StoryStatus, TaskStatus, SprintStatus,
    DeploymentStatus, ReviewSeverity, AGENT_ROLES,
)

__all__ = [
    "Factory",
    "BacklogManager",
    "ProductPlanner", "ProductPlan",
    "SprintEngine", "SprintResult",
    "ArchitectureGuardian", "GuardianReport", "ArchViolation",
    "CodeReviewEngine", "ReviewReport", "ReviewFinding",
    "TestingPipeline", "PipelineResult", "TestSuiteResult",
    "BrowserQA", "BrowserQAReport", "QACheck",
    "DeploymentPipeline", "DeploymentResult",
    "MaintenanceEngine", "MaintenanceReport", "MaintenanceIssue",
    "ExecutiveDashboard", "DashboardState",
    "EpicStatus", "StoryStatus", "TaskStatus", "SprintStatus",
    "DeploymentStatus", "ReviewSeverity", "AGENT_ROLES",
]
