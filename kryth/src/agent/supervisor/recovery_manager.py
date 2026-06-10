"""Recovery Manager — coordinated cross-agent recovery.

Orchestrates recovery across all supervisor subsystems:

  1. Detect the failure class (agent crash, terminal hang, browser drop, etc.)
  2. Pick the recovery strategy (auto-fix, replan, restart, escalate)
  3. Execute the recovery action
  4. Record the outcome for the ExperienceEngine

Recovery events are emitted to the timeline UI and persisted to the
ExperienceEngine so future runs avoid the same failure.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RecoveryClass(str, Enum):
    AGENT_CRASH = "agent_crash"
    TERMINAL_HANG = "terminal_hang"
    BROWSER_DROP = "browser_drop"
    DEPENDENCY_MISSING = "dependency_missing"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    MERGE_CONFLICT = "merge_conflict"
    UNKNOWN = "unknown"


@dataclass
class RecoveryEvent:
    failure_class: RecoveryClass
    description: str
    agent_id: Optional[str] = None
    command: Optional[str] = None
    error_output: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    resolved: bool = False
    resolution: str = ""
    recovery_command: Optional[str] = None


class RecoveryManager:
    """Coordinate multi-subsystem recovery from runtime failures."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: List[RecoveryEvent] = []
        self._recovery_count: int = 0
        self._max_retries: int = 3

    def handle(
        self,
        failure_class: RecoveryClass,
        description: str,
        *,
        agent_id: Optional[str] = None,
        command: Optional[str] = None,
        error_output: Optional[str] = None,
        project_root: str = ".",
    ) -> RecoveryEvent:
        """Handle a failure and attempt recovery. Returns the recovery event."""
        event = RecoveryEvent(
            failure_class=failure_class,
            description=description,
            agent_id=agent_id,
            command=command,
            error_output=error_output,
        )

        # Timeline notification
        try:
            from agent import ui
            ui.timeline_event(f"Recovery needed: {description}", "error")
        except Exception:
            pass

        # Dispatch recovery action
        self._recover(event, project_root)

        # Record to experience engine
        self._record_experience(event, project_root)

        with self._lock:
            self._events.append(event)
            if event.resolved:
                self._recovery_count += 1

        return event

    def _recover(self, event: RecoveryEvent, project_root: str) -> None:
        handler = {
            RecoveryClass.AGENT_CRASH: self._recover_agent_crash,
            RecoveryClass.TERMINAL_HANG: self._recover_terminal_hang,
            RecoveryClass.BROWSER_DROP: self._recover_browser_drop,
            RecoveryClass.DEPENDENCY_MISSING: self._recover_dependency,
            RecoveryClass.TIMEOUT: self._recover_timeout,
            RecoveryClass.MERGE_CONFLICT: self._recover_merge_conflict,
        }.get(event.failure_class)

        if handler:
            handler(event, project_root)
        else:
            event.resolution = "no handler for this failure class"

    def _recover_agent_crash(self, event: RecoveryEvent, project_root: str) -> None:
        from agent.supervisor.agent_supervisor import agent_supervisor
        from agent.supervisor.replanner import dynamic_replanner, ReplanEvent

        # Mark the agent crashed
        if event.agent_id:
            agent_supervisor.mark_crashed(event.agent_id, event.description)

        # Get all running agents as candidates for reassignment
        statuses = agent_supervisor.all_statuses()
        available = [s.agent_id for s in statuses
                     if s.status in ("running", "idle") and s.agent_id != event.agent_id]

        replan = dynamic_replanner.replan(
            ReplanEvent(
                reason=event.description,
                failed_agent=event.agent_id,
                remaining_tasks=[],
                available_agents=available,
            ),
            project_root,
        )

        event.resolved = replan.success
        event.resolution = replan.reason

        try:
            from agent import ui
            if replan.success:
                ui.timeline_event(f"Agent crash recovered: {replan.reason}", "success")
            else:
                ui.timeline_event(f"Agent crash — could not recover: {replan.reason}", "error")
        except Exception:
            pass

    def _recover_terminal_hang(self, event: RecoveryEvent, project_root: str) -> None:
        from agent.supervisor.terminal_supervisor import terminal_supervisor
        actions = terminal_supervisor.sweep()
        event.resolved = bool(actions)
        event.resolution = "; ".join(actions) if actions else "no hung processes found"

    def _recover_browser_drop(self, event: RecoveryEvent, project_root: str) -> None:
        from agent.supervisor.browser_supervisor import browser_supervisor
        success = browser_supervisor.attempt_recovery()
        event.resolved = success
        event.resolution = "browser session restored" if success else "browser recovery failed"

    def _recover_dependency(self, event: RecoveryEvent, project_root: str) -> None:
        """Use the existing RecoveryEngine to find + apply a fix."""
        try:
            from agent.terminal.output_parser import output_parser
            from agent.terminal.recovery import RecoveryEngine
            from agent.terminal.shell import get_shell

            raw = event.error_output or event.description
            parsed = output_parser.parse(raw, exit_code=1)
            engine = RecoveryEngine()
            actions = engine.suggest(parsed, project_root)

            if actions:
                action = actions[0]
                shell = get_shell()
                out, rc = shell.execute(action.command, timeout=120.0)
                event.resolved = rc == 0
                event.resolution = f"applied: {action.command}"
                event.recovery_command = action.command
            else:
                event.resolution = "no automatic repair found"
        except Exception as exc:
            event.resolution = f"recovery error: {exc}"

    def _recover_timeout(self, event: RecoveryEvent, project_root: str) -> None:
        # Kill the specific process if PID is known
        from agent.supervisor.terminal_supervisor import terminal_supervisor
        actions = terminal_supervisor.sweep()
        event.resolved = bool(actions)
        event.resolution = "; ".join(actions) if actions else "timeout handled"

    def _recover_merge_conflict(self, event: RecoveryEvent, project_root: str) -> None:
        from agent.supervisor.ownership import ownership_enforcer
        event.resolution = "merge conflict — check ownership table"
        snap = ownership_enforcer.snapshot()
        if snap:
            conflicts = [k for k in snap if "file:" in k]
            event.resolution = (
                f"conflict on {len(conflicts)} file(s); "
                "serialize agents via ownership locks"
            )
        event.resolved = False  # needs human or replanner

    def _record_experience(self, event: RecoveryEvent, project_root: str) -> None:
        """Record the recovery outcome to the experience engine."""
        try:
            from agent.experience import get_experience
            exp = get_experience(project_root)
            if event.recovery_command:
                exp.learn(
                    "repair",
                    error_type=event.failure_class.value,
                    error_message=event.description[:200],
                    repair_commands=[event.recovery_command],
                    success=event.resolved,
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Query

    def all_events(self) -> List[RecoveryEvent]:
        with self._lock:
            return list(self._events)

    def recovery_count(self) -> int:
        with self._lock:
            return self._recovery_count

    def unresolved(self) -> List[RecoveryEvent]:
        with self._lock:
            return [e for e in self._events if not e.resolved]

    def classify_error(self, output: str, exit_code: int = 1) -> RecoveryClass:
        """Infer failure class from raw error output."""
        lower = output.lower()
        if "module not found" in lower or "cannot find module" in lower \
                or "no module named" in lower:
            return RecoveryClass.DEPENDENCY_MISSING
        if "permission denied" in lower or "access denied" in lower:
            return RecoveryClass.PERMISSION_DENIED
        if "timeout" in lower or "timed out" in lower:
            return RecoveryClass.TIMEOUT
        if "conflict" in lower or "merge" in lower:
            return RecoveryClass.MERGE_CONFLICT
        if exit_code == 130:  # SIGINT
            return RecoveryClass.TIMEOUT
        return RecoveryClass.UNKNOWN


# Module-level singleton
recovery_manager = RecoveryManager()
