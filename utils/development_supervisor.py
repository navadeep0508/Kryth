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

# Benchmark phase status values — independent of task checkpoint step statuses.
BENCHMARK_PENDING = "pending"
BENCHMARK_RUNNING = "running"
BENCHMARK_COMPLETED = "completed"
BENCHMARK_FAILED = "failed"
BENCHMARK_SKIPPED = "skipped"
BENCHMARK_VERIFIED = "verified"

_BENCHMARK_TERMINAL = {BENCHMARK_COMPLETED, BENCHMARK_FAILED, BENCHMARK_SKIPPED, BENCHMARK_VERIFIED}
_BENCHMARK_FINAL = {BENCHMARK_VERIFIED, BENCHMARK_SKIPPED}

# Authoritative task state values — the single source of truth for task lifecycle.
# These are authoritative: the task checkpoint status must always be one of these,
# and completion requires all benchmark phases to be verified.
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED_STATE = "completed"
TASK_FAILED = "failed"
TASK_BLOCKED = "blocked"
TASK_AWAITING_APPROVAL = "awaiting_approval"
TASK_VERIFICATION_REQUIRED = "verification_required"
TASK_VERIFIED = "verified"

_TASK_TERMINAL = {TASK_COMPLETED_STATE, TASK_FAILED, TASK_BLOCKED}
_TASK_VERIFIABLE = {TASK_COMPLETED_STATE, TASK_VERIFIED}


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
            "benchmark_phases": {},
            "files_changed": [], "commands_executed": [],
            "last_successful_verification": None, "last_error": None,
            "recovery_instructions": None,
            "task_checkpoint": {
                "task_id": None, "description": None, "status": TASK_PENDING,
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
            state.setdefault("benchmark_phases", self._empty()["benchmark_phases"])
            for phase in PHASES:
                state["phases"].setdefault(phase, self._empty()["phases"][phase])
            # Repair any inconsistency between task_checkpoint.status and
            # current_task_status so they are always in sync after loading.
            self._repair_status_inconsistency(state)
            return state
        except (OSError, ValueError, TypeError):
            return self._empty()

    @staticmethod
    def _repair_status_inconsistency(state: dict[str, Any]) -> None:
        """Ensure current_task_status mirrors task_checkpoint.status.

        If the two diverge (e.g. a crash happened between the two writes in a
        previous session) the checkpoint status is treated as authoritative
        because it is the only value validated by state-transition logic.
        """
        task = state.get("task_checkpoint") or {}
        checkpoint_status = task.get("status", TASK_PENDING)
        _status_map = {
            TASK_COMPLETED_STATE: "TASK_COMPLETED",
            TASK_FAILED: "TASK_FAILED",
            TASK_BLOCKED: "TASK_BLOCKED",
        }
        expected_top_level = _status_map.get(checkpoint_status)
        if expected_top_level is not None:
            state["current_task_status"] = expected_top_level
        elif "current_task_status" in state and checkpoint_status not in (
            TASK_COMPLETED_STATE, TASK_FAILED, TASK_BLOCKED
        ):
            # Remove a stale current_task_status that claims completion while
            # the checkpoint says the task is still running or pending.
            state.pop("current_task_status", None)

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
        if task.get("status") == TASK_RUNNING:
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
        # Only return early for a genuinely in-progress task. A completed or
        # failed task from a previous session must not block a new one.
        if task.get("status") == TASK_RUNNING:
            return str(task["task_id"])
        selected_phase = phase or self.state["current_phase"]
        if selected_phase not in PHASES:
            raise ValueError(f"Unknown development phase: {selected_phase}")
        task = {
            "task_id": str(uuid.uuid4()),
            "description": description.strip() or "Multi-step task",
            "status": TASK_RUNNING,
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
        if task.get("status") != TASK_RUNNING:
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
            # A previously failed step that is re-verified must be removed
            # from failed_steps so the task can complete.
            if description in task["failed_steps"]:
                task["failed_steps"].remove(description)
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
        # Authoritative state: task completes only when all steps are done AND
        # all benchmark phases are verified.
        finished = bool(task["steps"]) and not task["pending_steps"] and not task["failed_steps"]
        if finished and self.all_benchmark_phases_verified():
            task["status"] = TASK_COMPLETED_STATE
            task["recovery_information"] = None
            self.state["current_task_status"] = "TASK_COMPLETED"
        elif finished and not self.all_benchmark_phases_verified():
            task["status"] = TASK_VERIFICATION_REQUIRED
            task["recovery_information"] = "All plan steps complete but benchmark phases remain unverified."
        elif task["pending_steps"]:
            task["recovery_information"] = "Resume at the first pending step; inspect current state before repeating an action."
        self._persist()
        return finished

    def complete_task_checkpoint(self) -> None:
        """Complete the task checkpoint authoritatively.

        Requires: all plan steps completed with verification evidence AND
        all benchmark phases verified. Sets status to TASK_COMPLETED_STATE.
        """
        self.state = self._load()
        task = self.state["task_checkpoint"]

        # Check task checkpoint steps if any exist.
        if task.get("steps"):
            if task.get("pending_steps") or task.get("failed_steps"):
                raise ValueError("Task cannot complete while steps remain pending or failed")
            if any(step.get("status") != "completed" or not step.get("verification") for step in task["steps"]):
                raise ValueError("Task cannot complete without verified evidence for every step")

        # Check benchmark phases if any exist — these are the user-requested
        # work items and must all be verified before the task can complete.
        if self.state["benchmark_phases"]:
            if not self.all_benchmark_phases_verified():
                raise ValueError("Task cannot complete while benchmark phases remain unverified")

        # A task with neither steps nor phases has nothing to complete.
        if not task.get("steps") and not self.state["benchmark_phases"]:
            raise ValueError("Task has no steps or benchmark phases to complete")

        task["status"] = TASK_COMPLETED_STATE
        self.state["current_task_status"] = "TASK_COMPLETED"
        self._persist()

    # ------------------------------------------------------------------
    # Authoritative task state management
    # ------------------------------------------------------------------

    def get_task_status(self) -> str:
        """Return the current authoritative task status."""
        return (self.state.get("task_checkpoint") or {}).get("status", TASK_PENDING)

    def set_task_status(self, status: str, reason: str | None = None) -> None:
        """Set the authoritative task status.

        Valid statuses: PENDING, RUNNING, COMPLETED, FAILED, BLOCKED,
        AWAITING_APPROVAL, VERIFICATION_REQUIRED, VERIFIED.
        """
        valid = {
            TASK_PENDING, TASK_RUNNING, TASK_COMPLETED_STATE, TASK_FAILED,
            TASK_BLOCKED, TASK_AWAITING_APPROVAL, TASK_VERIFICATION_REQUIRED,
            TASK_VERIFIED,
        }
        if status not in valid:
            raise ValueError(f"Invalid task status: {status}. Must be one of {sorted(valid)}")
        self.state = self._load()
        task = self.state["task_checkpoint"]
        old_status = task.get("status")
        task["status"] = status
        if reason:
            task["recovery_information"] = reason
        if status == TASK_COMPLETED_STATE:
            self.state["current_task_status"] = "TASK_COMPLETED"
        elif status == TASK_FAILED:
            self.state["current_task_status"] = "TASK_FAILED"
        elif status == TASK_BLOCKED:
            self.state["current_task_status"] = "TASK_BLOCKED"
        self._persist()

    def is_task_complete(self) -> bool:
        """Return True if the task is in a completed state."""
        return self.get_task_status() in _TASK_VERIFIABLE

    def is_task_verified(self) -> bool:
        """Return True if the task is verified (completed and all benchmark phases verified)."""
        return self.get_task_status() == TASK_VERIFIED or (
            self.get_task_status() == TASK_COMPLETED_STATE
            and self.all_benchmark_phases_verified()
        )

    def block_task(self, reason: str) -> None:
        """Block the task with a reason; requires user action to unblock."""
        self.set_task_status(TASK_BLOCKED, reason)

    def unblock_task(self) -> None:
        """Unblock a blocked task, returning it to running state."""
        self.set_task_status(TASK_RUNNING, "Task unblocked; resuming from last verified state.")

    def await_approval(self, reason: str) -> None:
        """Set the task to awaiting approval state."""
        self.set_task_status(TASK_AWAITING_APPROVAL, reason)

    def require_verification(self, reason: str) -> None:
        """Set the task to verification-required state."""
        self.set_task_status(TASK_VERIFICATION_REQUIRED, reason)

    def fail_task_checkpoint(self, error: str, recovery: str) -> None:
        """Mark the task as failed with error and recovery instructions."""
        self.set_task_status(TASK_FAILED, f"{error} | Recovery: {recovery}")

    # ------------------------------------------------------------------
    # Benchmark phase tracking — independent of task checkpoint steps.
    # Benchmark phases represent user-requested work items; plan steps
    # represent internal implementation tasks. The two are tracked
    # separately so that completing internal plan steps never incorrectly
    # marks the user's task as complete.
    # ------------------------------------------------------------------

    def add_benchmark_phase(self, phase_id: str, description: str) -> None:
        """Add a benchmark phase with a stable unique ID.

        Duplicate phase IDs are ignored (no double-counting).
        """
        self.state = self._load()
        if phase_id in self.state["benchmark_phases"]:
            return
        self.state["benchmark_phases"][phase_id] = {
            "id": phase_id,
            "description": description,
            "status": BENCHMARK_PENDING,
            "evidence": None,
            "verified_at": None,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "recovery": None,
        }
        self._persist()

    def start_benchmark_phase(self, phase_id: str) -> None:
        """Mark a benchmark phase as running."""
        self.state = self._load()
        phase = self.state["benchmark_phases"].get(phase_id)
        if phase is None:
            raise ValueError(f"Benchmark phase not found: {phase_id}")
        phase["status"] = BENCHMARK_RUNNING
        phase["started_at"] = self._now()
        self._persist()

    def complete_benchmark_phase(self, phase_id: str, evidence: str) -> None:
        """Mark a benchmark phase as completed with execution evidence.

        Evidence is required — a phase cannot be completed on intention alone.
        """
        if not evidence or not evidence.strip():
            raise ValueError("Execution evidence is required to complete a benchmark phase")
        self.state = self._load()
        phase = self.state["benchmark_phases"].get(phase_id)
        if phase is None:
            raise ValueError(f"Benchmark phase not found: {phase_id}")
        phase["status"] = BENCHMARK_COMPLETED
        phase["evidence"] = evidence.strip()
        phase["completed_at"] = self._now()
        self._persist()

    def verify_benchmark_phase(self, phase_id: str, evidence: str) -> None:
        """Verify a completed benchmark phase.

        Verification is a separate step from completion — a phase must be
        completed before it can be verified, and verification requires
        successful confirmation evidence.
        """
        self.state = self._load()
        phase = self.state["benchmark_phases"].get(phase_id)
        if phase is None:
            raise ValueError(f"Benchmark phase not found: {phase_id}")
        if phase["status"] != BENCHMARK_COMPLETED:
            raise ValueError(
                f"Benchmark phase {phase_id} is not completed "
                f"(current status: {phase['status']}); cannot verify"
            )
        phase["status"] = BENCHMARK_VERIFIED
        phase["verified_at"] = self._now()
        # Preserve the original completion evidence; verification is a
        # separate confirmation that does not replace it.
        if evidence and evidence.strip():
            phase["verification_evidence"] = evidence.strip()
        self._persist()

    def fail_benchmark_phase(self, phase_id: str, error: str, recovery: str) -> None:
        """Mark a benchmark phase as failed with recovery instructions."""
        self.state = self._load()
        phase = self.state["benchmark_phases"].get(phase_id)
        if phase is None:
            raise ValueError(f"Benchmark phase not found: {phase_id}")
        phase["status"] = BENCHMARK_FAILED
        phase["error"] = error
        phase["recovery"] = recovery
        self._persist()

    def skip_benchmark_phase(self, phase_id: str) -> None:
        """Mark a benchmark phase as skipped."""
        self.state = self._load()
        phase = self.state["benchmark_phases"].get(phase_id)
        if phase is None:
            raise ValueError(f"Benchmark phase not found: {phase_id}")
        phase["status"] = BENCHMARK_SKIPPED
        self._persist()

    def get_benchmark_phase(self, phase_id: str) -> dict | None:
        """Get a benchmark phase's details by ID."""
        return self.state["benchmark_phases"].get(phase_id)

    def get_all_benchmark_phases(self) -> list[dict]:
        """Return all benchmark phases in insertion order."""
        return list(self.state["benchmark_phases"].values())

    def get_benchmark_progress(self) -> dict[str, int]:
        """Return benchmark phase progress counts by status."""
        phases = self.state["benchmark_phases"]
        return {
            "total": len(phases),
            "pending": sum(1 for p in phases.values() if p["status"] == BENCHMARK_PENDING),
            "running": sum(1 for p in phases.values() if p["status"] == BENCHMARK_RUNNING),
            "completed": sum(1 for p in phases.values() if p["status"] == BENCHMARK_COMPLETED),
            "failed": sum(1 for p in phases.values() if p["status"] == BENCHMARK_FAILED),
            "skipped": sum(1 for p in phases.values() if p["status"] == BENCHMARK_SKIPPED),
            "verified": sum(1 for p in phases.values() if p["status"] == BENCHMARK_VERIFIED),
        }

    def has_incomplete_benchmark_phases(self) -> bool:
        """Return True if any benchmark phase is not verified or skipped.

        A phase that is completed but not yet verified is still incomplete
        from the task's perspective — verification is required.
        """
        return any(
            p["status"] not in _BENCHMARK_FINAL
            for p in self.state["benchmark_phases"].values()
        )

    def all_benchmark_phases_verified(self) -> bool:
        """Return True if every benchmark phase is verified or skipped.

        Vacuously true when no benchmark phases exist.
        """
        phases = self.state["benchmark_phases"]
        if not phases:
            return True
        return all(
            p["status"] in {BENCHMARK_VERIFIED, BENCHMARK_SKIPPED}
            for p in phases.values()
        )

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
        task["status"] = TASK_RUNNING
        self._persist()

    def cancel_task_checkpoint(self, reason: str) -> None:
        self.state = self._load()
        task = self.state["task_checkpoint"]
        if task.get("status") == TASK_RUNNING:
            task["status"] = "cancelled"
            task["recovery_information"] = reason
            self._persist()

    def start_phase(self, phase: str) -> None:
        self.state = self._load()
        if phase not in PHASES:
            raise ValueError(f"Unknown development phase: {phase}")
        self.state["current_phase"] = phase
        task = self.state.get("task_checkpoint") or {}
        if task.get("status") == TASK_RUNNING:
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


__all__ = [
    "DevelopmentSupervisor", "PHASES",
    "TASK_PENDING", "TASK_RUNNING", "TASK_COMPLETED_STATE", "TASK_FAILED",
    "TASK_BLOCKED", "TASK_AWAITING_APPROVAL", "TASK_VERIFICATION_REQUIRED",
    "TASK_VERIFIED",
]
