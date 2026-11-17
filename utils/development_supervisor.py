"""Persistent development phase and recovery checkpoint tracking."""

from __future__ import annotations

import json
import os
import re
import uuid
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
            "task_checkpoint": {
                "task_id": None, "description": None, "status": "idle",
                "current_phase": PHASES[0], "steps": [],
                "completed_steps": [], "pending_steps": [], "failed_steps": [],
                "last_verified_state": None, "recovery_information": None,
            },
        }

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            state = self._empty()
            state.update(data)
            state.setdefault("task_checkpoint", self._empty()["task_checkpoint"])
            for phase in PHASES:
                state["phases"].setdefault(phase, self._empty()["phases"][phase])
            return state
        except (OSError, ValueError, TypeError):
            return self._empty()

    def _persist(self) -> None:
        self.state["checkpointed_at"] = self._now()
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
        task = state.get("task_checkpoint") or {}
        if task.get("status") == "in_progress":
            lines.append(f"- Task checkpoint: {task.get('task_id')} (in progress)")
            lines.append(f"- Task: {task.get('description')}")
            if task.get("pending_steps"):
                lines.append("- Pending steps: " + "; ".join(task["pending_steps"]))
            if task.get("failed_steps"):
                lines.append("- Failed steps: " + "; ".join(task["failed_steps"]))
            if task.get("last_verified_state"):
                lines.append(f"- Last verified state: {task['last_verified_state']}")
            if task.get("recovery_information"):
                lines.append(f"- Task recovery: {task['recovery_information']}")
        return "\n".join(lines)

    def begin_task(self, description: str, phase: str | None = None) -> str:
        self.state = self._load()
        task = self.state["task_checkpoint"]
        if task.get("status") == "in_progress":
            return str(task["task_id"])
        selected_phase = phase or self.state["current_phase"]
        if selected_phase not in PHASES:
            raise ValueError(f"Unknown development phase: {selected_phase}")
        task = {
            "task_id": str(uuid.uuid4()),
            "description": description.strip() or "Multi-step task",
            "status": "in_progress",
            "current_phase": selected_phase,
            "steps": [],
            "completed_steps": [],
            "pending_steps": [],
            "failed_steps": [],
            "last_verified_state": None,
            "recovery_information": "Resume at the first pending step; inspect current state before repeating an action.",
        }
        self.state["task_checkpoint"] = task
        self.state["current_task_id"] = task["task_id"]
        self.state["current_task"] = task["description"]
        self.state["current_phase"] = selected_phase
        self._persist()
        return task["task_id"]

    def add_task_step(self, step_id: str, description: str) -> None:
        self.state = self._load()
        task = self.state["task_checkpoint"]
        if task.get("status") != "in_progress":
            self.begin_task(description)
            self.state = self._load()
            task = self.state["task_checkpoint"]
        if any(step.get("id") == step_id for step in task["steps"]):
            return
        step = {"id": step_id, "description": description, "status": "pending"}
        task["steps"].append(step)
        task["pending_steps"].append(description)
        self.state["current_task_id"] = task["task_id"]
        self.state["current_task"] = description
        self._persist()

    def verify_task_step(self, step_id: str, evidence: str, passed: bool = True) -> bool:
        if not evidence.strip():
            raise ValueError("Verification evidence is required")
        self.state = self._load()
        task = self.state["task_checkpoint"]
        step = next((item for item in task["steps"] if item["id"] == step_id), None)
        if step is None:
            raise ValueError(f"Task step not found: {step_id}")
        description = step["description"]
        step["verification"] = evidence.strip()
        if passed:
            step["status"] = "completed"
            if description in task["pending_steps"]:
                task["pending_steps"].remove(description)
            if description not in task["completed_steps"]:
                task["completed_steps"].append(description)
            task["last_verified_state"] = evidence.strip()
            self.state["last_successful_verification"] = {
                "task": description, "details": evidence.strip(), "at": self._now(),
            }
        else:
            step["status"] = "failed"
            if description in task["pending_steps"]:
                task["pending_steps"].remove(description)
            if description not in task["failed_steps"]:
                task["failed_steps"].append(description)
            task["recovery_information"] = evidence.strip()
            self.state["last_error"] = {
                "task": description, "error": evidence.strip(), "at": self._now(),
            }
        finished = bool(task["steps"]) and not task["pending_steps"] and not task["failed_steps"]
        if finished:
            task["status"] = "completed"
            task["recovery_information"] = None
            self.state["current_task_status"] = "TASK_COMPLETED"
        elif task["pending_steps"]:
            task["recovery_information"] = "Resume at the first pending step; inspect current state before repeating an action."
        self._persist()
        return finished

    def complete_task_checkpoint(self) -> None:
        self.state = self._load()
        task = self.state["task_checkpoint"]
        if not task.get("steps") or task.get("pending_steps") or task.get("failed_steps"):
            raise ValueError("Task cannot complete while steps remain pending or failed")
        if any(step.get("status") != "completed" or not step.get("verification") for step in task["steps"]):
            raise ValueError("Task cannot complete without verified evidence for every step")
        task["status"] = "completed"
        self.state["current_task_status"] = "TASK_COMPLETED"
        self._persist()

    def fail_task_step(self, step_id: str, error: str, recovery: str) -> None:
        self.state = self._load()
        task = self.state["task_checkpoint"]
        step = next((item for item in task["steps"] if item["id"] == step_id), None)
        if step is None:
            raise ValueError(f"Task step not found: {step_id}")
        description = step["description"]
        step["status"] = "failed"
        step["error"] = error
        step["recovery"] = recovery
        if description in task["pending_steps"]:
            task["pending_steps"].remove(description)
        if description not in task["failed_steps"]:
            task["failed_steps"].append(description)
        task["recovery_information"] = recovery
        task["status"] = "in_progress"
        self._persist()

    def cancel_task_checkpoint(self, reason: str) -> None:
        self.state = self._load()
        task = self.state["task_checkpoint"]
        if task.get("status") == "in_progress":
            task["status"] = "cancelled"
            task["recovery_information"] = reason
            self._persist()

    def start_phase(self, phase: str) -> None:
        self.state = self._load()
        if phase not in PHASES:
            raise ValueError(f"Unknown development phase: {phase}")
        self.state["current_phase"] = phase
        task = self.state.get("task_checkpoint") or {}
        if task.get("status") == "in_progress":
            task["current_phase"] = phase
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
