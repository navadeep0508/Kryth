"""Persistent development phase and recovery checkpoint tracking."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PHASES = (
    "repository_inspection", "architecture_analysis", "backend_development",
    "frontend_development", "integration", "testing", "security_review",
    "deployment_preparation",
)


class DevelopmentSupervisor:
    """Store verifiable task state under the active workspace's `.kryth` dir."""

    def __init__(self, workspace: Path | str) -> None:
        self.workspace = Path(workspace).resolve()
        self.path = self.workspace / ".kryth" / "development_checkpoint.json"
        self.state: dict[str, Any] = self._load()

    def _empty(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "current_project": self.workspace.name,
            "current_phase": PHASES[0],
            "current_task": None,
            "phases": {name: {"planned": [], "completed": [], "failed": [], "verification_results": [], "pending": []} for name in PHASES},
            "files_changed": [], "commands_executed": [],
            "last_successful_verification": None, "last_error": None,
            "recovery_instructions": None,
        }

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            state = self._empty()
            state.update(data)
            for phase in PHASES:
                state["phases"].setdefault(phase, self._empty()["phases"][phase])
            return state
        except (OSError, ValueError, TypeError):
            return self._empty()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def checkpoint(self, **facts: Any) -> None:
        self.state["checkpointed_at"] = self._now()
        self.state.update(facts)
        self._persist()

    def resume_summary(self) -> str | None:
        state = self._load()
        if not state.get("checkpointed_at") and not state.get("commands_executed"):
            return None
        lines = ["Development checkpoint:"]
        lines.append(f"- Project: {state.get('current_project') or self.workspace.name}")
        lines.append(f"- Phase: {state.get('current_phase')}")
        if state.get("current_task"):
            lines.append(f"- Current task: {state['current_task']}")
        phase = state.get("phases", {}).get(state.get("current_phase"), {})
        pending = phase.get("pending", [])
        if pending:
            lines.append("- Pending tasks: " + "; ".join(pending))
        verification = state.get("last_successful_verification")
        if verification:
            lines.append(f"- Last verified result: {verification.get('details') or verification.get('description')}")
        error = state.get("last_error")
        if error:
            lines.append(f"- Last error: {error.get('error')}")
        if state.get("recovery_instructions"):
            lines.append(f"- Recovery: {state['recovery_instructions']}")
        return "\n".join(lines)

    def start_phase(self, phase: str) -> None:
        self.state = self._load()
        if phase not in PHASES:
            raise ValueError(f"Unknown development phase: {phase}")
        self.state["current_phase"] = phase
        self._persist()

    def plan_task(self, task: str, phase: str | None = None) -> None:
        self.state = self._load()
        phase = phase or self.state["current_phase"]
        bucket = self.state["phases"][phase]
        if task not in bucket["planned"]:
            bucket["planned"].append(task)
        if task not in bucket["completed"] and task not in bucket["failed"] and task not in bucket["pending"]:
            bucket["pending"].append(task)
        self.state["current_phase"], self.state["current_task"] = phase, task
        self._persist()

    def complete_task(self, task: str, verification: str, phase: str | None = None) -> None:
        if not verification.strip():
            raise ValueError("A successful verification is required to complete a task")
        self.state = self._load()
        phase = phase or self.state["current_phase"]
        bucket = self.state["phases"][phase]
        if task not in bucket["planned"]:
            bucket["planned"].append(task)
        if task in bucket["pending"]:
            bucket["pending"].remove(task)
        if task not in bucket["completed"]:
            bucket["completed"].append(task)
        bucket["verification_results"].append({"task": task, "passed": True, "details": verification, "at": self._now()})
        self.state["last_successful_verification"] = {"task": task, "details": verification, "at": self._now()}
        self._persist()

    def fail_task(self, task: str, error: str, recovery: str, phase: str | None = None) -> None:
        self.state = self._load()
        phase = phase or self.state["current_phase"]
        bucket = self.state["phases"][phase]
        if task in bucket["pending"]:
            bucket["pending"].remove(task)
        if task not in bucket["failed"]:
            bucket["failed"].append(task)
        self.state["last_error"] = {"task": task, "error": error, "at": self._now()}
        self.state["recovery_instructions"] = recovery
        self._persist()

    def record_command(self, command: str, cwd: Path | str, exit_code: int | None, error: str | None = None) -> None:
        self.state = self._load()
        safe = re.sub(r"(?i)(api[_-]?key|token|password|secret)(\s*[=:]\s*)([^\s;]+)", r"\1\2[redacted]", command)
        self.state["commands_executed"].append({"command": safe, "cwd": str(cwd), "exit_code": exit_code, "at": self._now()})
        if error:
            self.state["last_error"] = {"task": self.state.get("current_task"), "error": error, "at": self._now()}
        self._persist()

    def record_file_change(self, path: Path | str) -> None:
        self.state = self._load()
        value = str(path)
        if value not in self.state["files_changed"]:
            self.state["files_changed"].append(value)
        self._persist()

    def record_verification(self, description: str, passed: bool, details: str = "") -> None:
        self.state = self._load()
        item = {"description": description, "passed": passed, "details": details, "at": self._now()}
        self.state["phases"][self.state["current_phase"]]["verification_results"].append(item)
        if passed:
            self.state["last_successful_verification"] = item
        else:
            self.state["last_error"] = {"task": self.state.get("current_task"), "error": details or description, "at": self._now()}
        self._persist()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["DevelopmentSupervisor", "PHASES"]
