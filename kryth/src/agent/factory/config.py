"""Autonomous Software Factory — constants and enumerations."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

KRYTH_HOME = Path(os.environ.get("KRYTH_HOME", str(Path.home() / ".kryth")))
FACTORY_DIR = KRYTH_HOME / "factory"
BACKLOG_DB_DIR = FACTORY_DIR / "backlogs"
SPRINTS_DB_DIR = FACTORY_DIR / "sprints"
REVIEWS_DIR = FACTORY_DIR / "reviews"
DEPLOYMENTS_DIR = FACTORY_DIR / "deployments"


class EpicStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class StoryStatus(str, Enum):
    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


class TaskStatus(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class SprintStatus(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    REVIEW = "review"
    COMPLETED = "completed"
    ABORTED = "aborted"


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    BUILDING = "building"
    DEPLOYING = "deploying"
    HEALTHY = "healthy"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class ReviewSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# Agent roles available to the factory
AGENT_ROLES = [
    "architect", "backend", "frontend", "database",
    "testing", "devops", "security", "documentation", "repair", "browser_qa",
]

# Default sprint duration (seconds) — 0 means no time limit
DEFAULT_SPRINT_DURATION = 0

# Approval-required operations
APPROVAL_REQUIRED_OPS = [
    "production_deploy", "destructive_migration",
    "mass_delete", "architecture_rewrite", "rollback",
]

# Architecture patterns to enforce
ARCH_PATTERNS = {
    "no_circular_imports": True,
    "test_coverage_min": 0.0,   # set project-specifically
    "max_file_lines": 1000,
    "no_hardcoded_secrets": True,
}
