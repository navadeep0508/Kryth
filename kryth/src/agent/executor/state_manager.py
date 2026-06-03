"""State manager — tracks current task/page/step state during execution.

Maintains a consistent view of execution state that can be inspected,
serialized, and resumed after failures.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ExecutionState:
    """Snapshot of the current execution state."""

    current_step_id: str = ""
    completed_step_ids: list[str] = field(default_factory=list)
    failed_step_ids: list[str] = field(default_factory=list)
    page_url: str = ""
    page_title: str = ""
    last_error: str = ""
    step_results: dict[str, Any] = field(default_factory=dict)
    started_at: float = 0.0
    updated_at: float = 0.0
    session_id: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "ExecutionState":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class StateManager:
    """Manages execution state across steps.

    Provides methods to:
    - Track which step is currently running
    - Record step results
    - Track page state (URL, title)
    - Save/restore state for resumability
    """

    def __init__(self) -> None:
        self._state = ExecutionState()

    @property
    def state(self) -> ExecutionState:
        return self._state

    def start_execution(self) -> None:
        self._state.started_at = time.time()
        self._state.updated_at = time.time()

    def start_step(self, step_id: str, page_url: str = "", page_title: str = "") -> None:
        self._state.current_step_id = step_id
        self._state.page_url = page_url
        self._state.page_title = page_title
        self._state.updated_at = time.time()

    def complete_step(self, step_id: str, result: Any = None) -> None:
        if step_id not in self._state.completed_step_ids:
            self._state.completed_step_ids.append(step_id)
        self._state.step_results[step_id] = result
        self._state.updated_at = time.time()

    def fail_step(self, step_id: str, error: str) -> None:
        if step_id not in self._state.failed_step_ids:
            self._state.failed_step_ids.append(step_id)
        self._state.last_error = error
        self._state.updated_at = time.time()

    def update_page_state(self, url: str = "", title: str = "") -> None:
        if url:
            self._state.page_url = url
        if title:
            self._state.page_title = title
        self._state.updated_at = time.time()

    def current_step(self) -> str:
        return self._state.current_step_id

    def is_step_completed(self, step_id: str) -> bool:
        return step_id in self._state.completed_step_ids

    def is_step_failed(self, step_id: str) -> bool:
        return step_id in self._state.failed_step_ids

    def get_result(self, step_id: str) -> Any:
        return self._state.step_results.get(step_id)

    def reset(self) -> None:
        self._state = ExecutionState()

    def snapshot(self) -> str:
        """Return a JSON string of the current state for logging."""
        return json.dumps(self._state.to_dict(), indent=2, default=str)

    @property
    def progress_summary(self) -> str:
        total = len(self._state.completed_step_ids) + len(self._state.failed_step_ids)
        completed = len(self._state.completed_step_ids)
        failed = len(self._state.failed_step_ids)
        return f"{completed} completed, {failed} failed, {total} total steps"

    @property
    def is_running(self) -> bool:
        return bool(self._state.current_step_id) and self._state.started_at > 0

    @property
    def elapsed_seconds(self) -> float:
        if self._state.started_at:
            return time.time() - self._state.started_at
        return 0.0
