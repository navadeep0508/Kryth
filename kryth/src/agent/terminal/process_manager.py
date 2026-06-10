"""Phase 3 — Process Manager.

Track, monitor, stop, restart, and kill processes by PID or command pattern.
Uses psutil when available; falls back to subprocess-based inspection.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


def _psutil_available() -> bool:
    try:
        import psutil  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class ProcessInfo:
    pid: int
    name: str
    command: str
    status: str        # running / sleeping / zombie / stopped
    cpu_percent: float = 0.0
    ram_mb: float = 0.0
    ports: list[int] = field(default_factory=list)
    start_time: float = 0.0
    runtime_seconds: float = 0.0
    log_path: str = ""


class ProcessManager:
    """Tracks processes started by KRYTH and system processes."""

    def __init__(self) -> None:
        # pid → ProcessInfo for processes KRYTH explicitly tracks
        self._tracked: dict[int, ProcessInfo] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Tracking

    def track(self, pid: int, command: str = "", log_path: str = "") -> ProcessInfo | None:
        """Start tracking a process by PID."""
        info = self._probe(pid, command, log_path)
        if info:
            with self._lock:
                self._tracked[pid] = info
        return info

    def untrack(self, pid: int) -> None:
        with self._lock:
            self._tracked.pop(pid, None)

    # ------------------------------------------------------------------
    # Query

    def list_tracked(self) -> list[ProcessInfo]:
        with self._lock:
            pids = list(self._tracked.keys())
        result = []
        for pid in pids:
            info = self._probe(pid)
            if info:
                with self._lock:
                    self._tracked[pid] = info
                result.append(info)
            else:
                with self._lock:
                    self._tracked.pop(pid, None)
        return result

    def get(self, pid: int) -> ProcessInfo | None:
        return self._probe(pid)

    def find_by_port(self, port: int) -> Optional[ProcessInfo]:
        """Find the process listening on the given port."""
        if not _psutil_available():
            return None
        import psutil  # type: ignore
        for conn in psutil.net_connections(kind="inet"):
            if conn.status == "LISTEN" and conn.laddr and conn.laddr.port == port and conn.pid:
                return self._probe(conn.pid)
        return None

    def find_by_name(self, pattern: str) -> list[ProcessInfo]:
        """Find processes whose command contains pattern."""
        if not _psutil_available():
            return []
        import psutil  # type: ignore
        results = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmd = " ".join(proc.info.get("cmdline") or [])
                if pattern.lower() in cmd.lower():
                    info = self._probe(proc.info["pid"], cmd)
                    if info:
                        results.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return results

    # ------------------------------------------------------------------
    # Control

    def stop(self, pid: int, timeout: float = 5.0) -> bool:
        """Send SIGTERM, wait, then SIGKILL. Returns True if process ended."""
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not _is_alive(pid):
                    self.untrack(pid)
                    return True
                time.sleep(0.1)

            # Still alive — force kill
            return self.kill(pid)
        except (ProcessLookupError, PermissionError):
            return False

    def kill(self, pid: int) -> bool:
        """Force-kill a process."""
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, signal.SIGKILL)
            self.untrack(pid)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def restart(self, pid: int) -> Optional[int]:
        """Stop a tracked process and re-run its command. Returns new PID."""
        with self._lock:
            info = self._tracked.get(pid)
        if not info or not info.command:
            return None

        self.stop(pid)
        time.sleep(0.5)

        try:
            proc = subprocess.Popen(
                info.command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=None,
            )
            new_pid = proc.pid
            self.track(new_pid, info.command, info.log_path)
            return new_pid
        except Exception:
            return None

    def is_healthy(self, pid: int) -> bool:
        """Quick liveness check."""
        return _is_alive(pid)

    # ------------------------------------------------------------------
    # Internal

    def _probe(
        self, pid: int, command: str = "", log_path: str = ""
    ) -> Optional[ProcessInfo]:
        if not _is_alive(pid):
            return None

        if _psutil_available():
            return self._probe_psutil(pid, command, log_path)
        return ProcessInfo(
            pid=pid, name="", command=command, status="running",
            log_path=log_path,
        )

    def _probe_psutil(
        self, pid: int, command: str = "", log_path: str = ""
    ) -> Optional[ProcessInfo]:
        import psutil  # type: ignore
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                cmdline = proc.cmdline()
                cmd = command or " ".join(cmdline)
                status = proc.status()
                mem = proc.memory_info().rss / (1024 * 1024)
                cpu = proc.cpu_percent(interval=0.1)
                start = proc.create_time()
                runtime = time.time() - start

            ports: list[int] = []
            try:
                for conn in proc.connections(kind="inet"):
                    if conn.status == "LISTEN" and conn.laddr:
                        ports.append(conn.laddr.port)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass

            return ProcessInfo(
                pid=pid, name=proc.name(), command=cmd, status=status,
                cpu_percent=cpu, ram_mb=mem, ports=ports,
                start_time=start, runtime_seconds=runtime,
                log_path=log_path,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def format_table(self) -> str:
        """Format tracked processes as a text table."""
        infos = self.list_tracked()
        if not infos:
            return "(no tracked processes)"
        lines = ["PID     STATUS    CPU%   RAM_MB  PORTS  COMMAND"]
        for p in infos:
            ports = ",".join(str(x) for x in p.ports[:3]) or "-"
            cmd = p.command[:40]
            lines.append(
                f"{p.pid:<8}{p.status:<10}{p.cpu_percent:<7.1f}"
                f"{p.ram_mb:<8.1f}{ports:<7}{cmd}"
            )
        return "\n".join(lines)


def _is_alive(pid: int) -> bool:
    if _psutil_available():
        import psutil  # type: ignore
        return psutil.pid_exists(pid)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# Module-level singleton
process_manager = ProcessManager()
