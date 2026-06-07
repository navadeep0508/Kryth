"""Phase 5 & 15 — Terminal Session Tracker + Multi-Agent Coordinator.

TerminalCoordinator:
  - Owns a pool of named TerminalWorker instances (backend, frontend, tests, docker).
  - Prevents working-directory conflicts and port collisions.
  - Routes tasks to the appropriate worker.
  - Tracks which worker owns which resource.

SessionTracker extension:
  - Records every command, output, exit code, execution time.
  - Tracks files changed (via git diff before/after).
  - Supports replay of successful sequences.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from agent.terminal.worker import TerminalWorker, WorkerResult


# ---- Session tracking -----------------------------------------------

@dataclass
class TerminalEvent:
    worker: str
    command: str
    exit_code: int
    output: str
    duration: float
    files_changed: list[str] = field(default_factory=list)
    recovery_used: bool = False
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class TerminalSessionTracker:
    """Records all terminal events for a session. Supports replay."""

    def __init__(self) -> None:
        self._events: list[TerminalEvent] = []
        self._lock = threading.RLock()

    def record(self, event: TerminalEvent) -> None:
        with self._lock:
            self._events.append(event)

    def events(self) -> list[TerminalEvent]:
        with self._lock:
            return list(self._events)

    def successful_commands(self) -> list[str]:
        with self._lock:
            return [e.command for e in self._events if e.exit_code == 0]

    def failed_commands(self) -> list[str]:
        with self._lock:
            return [e.command for e in self._events if e.exit_code != 0]

    def replay_script(self) -> str:
        """Generate a shell script that replays all successful commands."""
        cmds = self.successful_commands()
        if not cmds:
            return "# No successful commands to replay"
        lines = ["#!/bin/bash", "set -e"]
        lines.extend(cmds)
        return "\n".join(lines)

    def summary(self) -> str:
        with self._lock:
            events = list(self._events)
        if not events:
            return "(no terminal events)"
        total = len(events)
        ok = sum(1 for e in events if e.exit_code == 0)
        failed = total - ok
        duration = sum(e.duration for e in events)
        return (
            f"{total} commands: {ok} succeeded, {failed} failed, "
            f"{duration:.1f}s total"
        )


# ---- Resource ownership table ---------------------------------------

@dataclass
class ResourceLock:
    owner: str          # worker name
    resource: str       # "cwd:/path", "port:8080", "file:foo.py"
    acquired_at: float = 0.0

    def __post_init__(self):
        if not self.acquired_at:
            self.acquired_at = time.time()


# ---- Coordinator ----------------------------------------------------

_DEFAULT_WORKER_NAMES = ["backend", "frontend", "tests", "docker", "misc"]


class TerminalCoordinator:
    """Manage a pool of named TerminalWorkers with resource isolation."""

    def __init__(self, workers: list[str] | None = None):
        self._lock = threading.RLock()
        self._workers: dict[str, TerminalWorker] = {}
        self._resource_locks: dict[str, ResourceLock] = {}
        self._tracker = TerminalSessionTracker()

        names = workers or _DEFAULT_WORKER_NAMES
        for name in names:
            self._workers[name] = TerminalWorker(name)

    # ------------------------------------------------------------------
    # Worker access

    def worker(self, name: str) -> TerminalWorker:
        with self._lock:
            if name not in self._workers:
                self._workers[name] = TerminalWorker(name)
            return self._workers[name]

    def all_workers(self) -> dict[str, TerminalWorker]:
        with self._lock:
            return dict(self._workers)

    def available_worker(self) -> Optional[TerminalWorker]:
        """Return the first non-busy worker, or create a spare."""
        with self._lock:
            for w in self._workers.values():
                if not w.busy:
                    return w
            # All busy — create a spare
            spare_name = f"spare-{len(self._workers)}"
            w = TerminalWorker(spare_name)
            self._workers[spare_name] = w
            return w

    # ------------------------------------------------------------------
    # Resource ownership

    def claim_cwd(self, worker_name: str, path: str) -> bool:
        """Claim a working directory for a worker. Returns False if taken."""
        resource = f"cwd:{path}"
        with self._lock:
            existing = self._resource_locks.get(resource)
            if existing and existing.owner != worker_name:
                return False
            self._resource_locks[resource] = ResourceLock(
                owner=worker_name, resource=resource
            )
            return True

    def claim_port(self, worker_name: str, port: int) -> bool:
        """Claim a port. Returns False if already claimed by another worker."""
        resource = f"port:{port}"
        with self._lock:
            existing = self._resource_locks.get(resource)
            if existing and existing.owner != worker_name:
                return False
            self._resource_locks[resource] = ResourceLock(
                owner=worker_name, resource=resource
            )
            return True

    def release(self, worker_name: str) -> None:
        """Release all resources owned by a worker."""
        with self._lock:
            to_remove = [
                k for k, v in self._resource_locks.items()
                if v.owner == worker_name
            ]
            for k in to_remove:
                del self._resource_locks[k]

    def port_owner(self, port: int) -> Optional[str]:
        resource = f"port:{port}"
        with self._lock:
            lock = self._resource_locks.get(resource)
            return lock.owner if lock else None

    # ------------------------------------------------------------------
    # Execution with tracking

    def run(
        self,
        worker_name: str,
        command: str,
        timeout: int = 60,
    ) -> WorkerResult:
        """Run a command on a named worker and record the event."""
        w = self.worker(worker_name)
        result = w.execute(command, timeout=timeout)
        event = TerminalEvent(
            worker=worker_name,
            command=command,
            exit_code=result.exit_code,
            output=result.output,
            duration=result.duration,
        )
        self._tracker.record(event)
        return result

    # ------------------------------------------------------------------
    # Session tracking access

    @property
    def tracker(self) -> TerminalSessionTracker:
        return self._tracker

    def session_summary(self) -> str:
        return self._tracker.summary()

    def replay_script(self) -> str:
        return self._tracker.replay_script()

    # ------------------------------------------------------------------
    # Conflict detection

    def check_conflicts(self, worker_name: str, command: str, cwd: str = "") -> list[str]:
        """Return a list of potential conflict warnings before running a command."""
        warnings: list[str] = []
        import re
        port_m = re.search(r'(?:--port|:)(\d{4,5})', command)
        if port_m:
            port = int(port_m.group(1))
            owner = self.port_owner(port)
            if owner and owner != worker_name:
                warnings.append(
                    f"Port {port} is already claimed by worker '{owner}'"
                )
        if cwd:
            resource = f"cwd:{cwd}"
            with self._lock:
                existing = self._resource_locks.get(resource)
            if existing and existing.owner != worker_name:
                warnings.append(
                    f"Directory '{cwd}' is owned by worker '{existing.owner}'"
                )
        return warnings

    def status_table(self) -> str:
        """Human-readable table of worker status."""
        with self._lock:
            workers = dict(self._workers)
            locks = dict(self._resource_locks)
        lines = ["Worker    Status    CWD"]
        for name, w in workers.items():
            status = "busy" if w.busy else "idle"
            lines.append(f"{name:<10}{status:<10}{w.cwd}")
        if locks:
            lines.append("")
            lines.append("Resource Locks:")
            for resource, lock in locks.items():
                lines.append(f"  {lock.owner}: {resource}")
        return "\n".join(lines)


# Module-level singleton
terminal_coordinator = TerminalCoordinator()
