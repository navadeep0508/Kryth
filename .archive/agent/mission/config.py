"""Mission system — constants and enumerations."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

KRYTH_HOME = Path(os.environ.get("KRYTH_HOME", str(Path.home() / ".kryth")))
MISSIONS_DIR = KRYTH_HOME / "missions"
CHECKPOINTS_DIR = KRYTH_HOME / "checkpoints"

MAX_CHECKPOINTS_PER_MISSION = 10

RISKY_COMMAND_PATTERNS = [
    "rm -rf", "DROP TABLE", "DELETE FROM", "git reset --hard",
    "git push --force", "kubectl delete", "terraform destroy",
    "shutil.rmtree", "os.remove", "truncate",
]


class MissionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class NodeStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    RETRYING = "retrying"
    SKIPPED = "skipped"


class ArtifactKind(str, Enum):
    FILE = "file"
    API_ROUTE = "api_route"
    COMPONENT = "component"
    DB_TABLE = "db_table"
    MODULE = "module"
    TEST = "test"
    CONFIG = "config"
    OTHER = "other"


class EntityKind(str, Enum):
    FILE = "file"
    DIRECTORY = "directory"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    API_ROUTE = "api_route"
    COMPONENT = "component"
    DB_TABLE = "db_table"
    SERVICE = "service"
    TEST = "test"
