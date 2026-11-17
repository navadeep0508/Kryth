from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any


class PanelStatus(str, Enum):
    RUNNING = "running"
    WAITING_APPROVAL = "waiting approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PanelState:
    """One tool call's presentation state, keyed by the agent call ID."""

    call_id: str
    name: str
    tool_kind: str | None
    arguments: dict[str, Any]
    status: PanelStatus = PanelStatus.RUNNING
    progress: str = ""
    started_at: float = field(default_factory=monotonic)
    elapsed: str | None = None
    summary: list[Any] = field(default_factory=list)
    details: list[Any] = field(default_factory=list)
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    diff: str | None = None
    exit_code: int | None = None
    truncated: bool = False

