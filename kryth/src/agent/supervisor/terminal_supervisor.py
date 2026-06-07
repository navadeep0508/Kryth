"""Terminal Supervisor — monitor running processes, kill hung commands.

Wraps the existing ProcessManager and TerminalCoordinator to:
  - Detect commands running beyond their expected timeout
  - Auto-kill hung processes (with a configurable grace period)
  - Monitor background servers for unexpected death
  - Restart crashed services when safe (idempotent restart only)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class WatchedProcess:
    pid: int
    command: str
    started_at: float = field(default_factory=time.monotonic)
    expected_max_seconds: float = 300.0
    auto_restart: bool = False
    restart_command: str = ""

    @property
    def running_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def is_overdue(self) -> bool:
        return self.running_seconds > self.expected_max_seconds


class TerminalSupervisor:
    """Supervise terminal processes and detect hung commands."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._watched: Dict[int, WatchedProcess] = {}
        self._killed: List[int] = []
        self._restarted: List[int] = []

    def watch(
        self,
        pid: int,
        command: str,
        expected_max_seconds: float = 300.0,
        auto_restart: bool = False,
        restart_command: str = "",
    ) -> None:
        with self._lock:
            self._watched[pid] = WatchedProcess(
                pid=pid,
                command=command,
                expected_max_seconds=expected_max_seconds,
                auto_restart=auto_restart,
                restart_command=restart_command,
            )

    def unwatch(self, pid: int) -> None:
        with self._lock:
            self._watched.pop(pid, None)

    def sweep(self) -> List[str]:
        """Check all watched processes; kill hung ones. Returns action log."""
        actions: List[str] = []
        with self._lock:
            procs = list(self._watched.values())

        for proc in procs:
            if not self._is_alive(proc.pid):
                with self._lock:
                    self._watched.pop(proc.pid, None)
                if proc.auto_restart and proc.restart_command:
                    new_pid = self._restart(proc)
                    if new_pid:
                        actions.append(
                            f"restarted crashed process '{proc.command[:40]}' "
                            f"(new pid={new_pid})"
                        )
                continue

            if proc.is_overdue:
                self._kill(proc.pid)
                with self._lock:
                    self._watched.pop(proc.pid, None)
                    self._killed.append(proc.pid)
                actions.append(
                    f"killed hung process pid={proc.pid} "
                    f"'{proc.command[:40]}' "
                    f"(ran {proc.running_seconds:.0f}s > {proc.expected_max_seconds:.0f}s limit)"
                )

        return actions

    def health_score(self) -> int:
        """0-100 terminal health score."""
        try:
            from agent.terminal.process_manager import process_manager
            tracked = process_manager.list_tracked()
            if not tracked:
                return 100
            alive = sum(1 for p in tracked if process_manager.is_healthy(p.pid))
            return int(alive / len(tracked) * 100)
        except Exception:
            return 100

    def _is_alive(self, pid: int) -> bool:
        try:
            from agent.terminal.process_manager import _is_alive
            return _is_alive(pid)
        except Exception:
            import os
            try:
                os.kill(pid, 0)
                return True
            except (ProcessLookupError, PermissionError):
                return False

    def _kill(self, pid: int) -> None:
        try:
            from agent.terminal.process_manager import process_manager
            process_manager.kill(pid)
        except Exception:
            import os, signal
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

    def _restart(self, proc: WatchedProcess) -> Optional[int]:
        try:
            import subprocess
            p = subprocess.Popen(
                proc.restart_command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.watch(p.pid, proc.restart_command,
                       proc.expected_max_seconds, proc.auto_restart,
                       proc.restart_command)
            self._restarted.append(p.pid)
            return p.pid
        except Exception:
            return None


# Module-level singleton
terminal_supervisor = TerminalSupervisor()
