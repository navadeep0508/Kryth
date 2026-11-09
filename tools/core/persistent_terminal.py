"""
KRYTH Persistent Terminal Manager.

Manages long-running terminal sessions for development servers and other
persistent processes. Each service gets its own managed terminal with
live output streaming, PID tracking, exit status, port detection, restart,
and stop functionality. Process-tree termination ensures no orphaned
processes are left behind.

Terminal sessions stay alive when switching between TUI screens.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TerminalStatus(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_FOR_INPUT = "waiting_for_input"
    EXITED = "exited"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# Patterns that indicate a development server has started successfully.
_STARTUP_SUCCESS_PATTERNS = [
    r"uvicorn running on",
    r"application startup complete",
    r"started server process",
    r"serving on",
    r"http://127\.0\.0\.1:",
    r"http://0\.0\.0\.0:",
    r"http://localhost:",
    r"dev server running",
    r"vite v\d",
    r"next dev",
    r"ready in",
    r"listening on",
    r"port \d+",
    r":\d+\s",
]

# Patterns to extract port numbers from output.
_PORT_PATTERNS = [
    r":(\d{4,5})\s",                          # :8000 followed by space
    r"port\s+(\d{4,5})",                      # port 8000
    r"127\.0\.0\.1:(\d{4,5})",                # 127.0.0.1:8000
    r"0\.0\.0\.0:(\d{4,5})",                  # 0.0.0.0:8000
    r"localhost:(\d{4,5})",                   # localhost:8000
]


@dataclass
class TerminalSession:
    """A single managed terminal session."""

    name: str
    command: str
    cwd: Path
    shell_type: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: TerminalStatus = TerminalStatus.IDLE
    pid: int | None = None
    exit_code: int | None = None
    started_at: float | None = None
    stopped_at: float | None = None
    output: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    process: Any | None = None  # asyncio.subprocess.Process
    restart_count: int = 0
    max_restarts: int = 3
    auto_restart: bool = False
    startup_timeout: float = 30.0  # seconds to wait for startup evidence


class PersistentTerminal:
    """A single persistent terminal session."""

    def __init__(self, session: TerminalSession) -> None:
        self.session = session
        self._read_task: Any | None = None
        self._output_callbacks: list[Any] = []
        _startup_monitor: Any | None = None

    @property
    def name(self) -> str:
        return self.session.name

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def status(self) -> TerminalStatus:
        return self.session.status

    @property
    def pid(self) -> int | None:
        return self.session.pid

    @property
    def exit_code(self) -> int | None:
        return self.session.exit_code

    @property
    def output(self) -> list[str]:
        return list(self.session.output)

    @property
    def ports(self) -> list[int]:
        return list(self.session.ports)

    @property
    def startup_timeout(self) -> float:
        return self.session.startup_timeout

    def add_output_callback(self, callback: Any) -> None:
        """Register a callback called with each new output line."""
        self._output_callbacks.append(callback)

    def _check_startup_success(self, text: str) -> bool:
        """Check if output text contains evidence of successful startup."""
        lowered = text.lower()
        for pattern in _STARTUP_SUCCESS_PATTERNS:
            if re.search(pattern, lowered):
                return True
        return False

    def _extract_ports(self, text: str) -> list[int]:
        """Extract port numbers from output text."""
        ports: list[int] = []
        for pattern in _PORT_PATTERNS:
            for match in re.finditer(pattern, text):
                try:
                    port = int(match.group(1))
                    if 1024 <= port <= 65535 and port not in ports:
                        ports.append(port)
                except (IndexError, ValueError):
                    continue
        return ports

    async def start(
        self,
        command: str | None = None,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        wait_for_startup: bool = True,
    ) -> bool:
        """Start the persistent process.

        If *wait_for_startup* is True, monitors output for evidence that
        the process started successfully before returning. The process
        continues running in the background regardless.
        """
        if self.session.status == TerminalStatus.RUNNING:
            return True

        cmd = command or self.session.command
        work_dir = cwd or self.session.cwd
        shell_env = os.environ.copy()
        if env:
            shell_env.update(env)

        self.session.status = TerminalStatus.STARTING
        self.session.started_at = time.monotonic()
        self.session.output = []
        self.session.ports = []

        try:
            from tools.core.shells import select_shell

            shell = select_shell(preferred=self.session.shell_type)
            args = shell.build_command(cmd, work_dir)
            process_options: dict[str, Any] = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.STDOUT,
                "cwd": work_dir,
                "env": shell_env,
            }
            if sys.platform == "win32":
                process_options["creationflags"] = getattr(
                    subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                )
            else:
                process_options["start_new_session"] = True
            process = await asyncio.create_subprocess_exec(*args, **process_options)

            self.session.process = process
            self.session.pid = process.pid
            self.session.stopped_at = None
            self.session.exit_code = None

            # Start reading output.
            self._read_task = asyncio.create_task(self._read_output(process.stdout))

            if wait_for_startup:
                # Wait briefly for startup evidence, but don't block indefinitely.
                # The process continues running regardless.
                try:
                    await asyncio.wait_for(
                        self._wait_for_startup(),
                        timeout=self.session.startup_timeout,
                    )
                except asyncio.TimeoutError:
                    # No startup evidence within timeout — still running but
                    # unconfirmed. Mark as RUNNING anyway since the process
                    # is alive.
                    if self.session.status == TerminalStatus.STARTING:
                        self.session.status = TerminalStatus.RUNNING
            else:
                if self.session.status == TerminalStatus.STARTING:
                    self.session.status = TerminalStatus.RUNNING

            return True

        except Exception as e:
            self.session.status = TerminalStatus.ERROR
            self.session.output.append(f"Error starting: {e}")
            return False

    async def _wait_for_startup(self) -> None:
        """Wait until startup evidence appears or process exits."""
        while self.session.status == TerminalStatus.STARTING:
            # Check if process has exited.
            if self.session.process and self.session.process.returncode is not None:
                self.session.status = TerminalStatus.FAILED
                self.session.exit_code = self.session.process.returncode
                return

            # Check recent output for startup evidence.
            recent = "".join(self.session.output[-10:]) if self.session.output else ""
            if self._check_startup_success(recent):
                self.session.status = TerminalStatus.RUNNING
                new_ports = self._extract_ports(recent)
                if new_ports:
                    self.session.ports.extend(new_ports)
                return

            await asyncio.sleep(0.5)

    async def _read_output(self, stream: Any) -> None:
        """Read output from the process stream."""
        import codecs
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                data = await stream.read(8192)
                if not data:
                    break
                text = decoder.decode(data)
                if text:
                    self.session.output.append(text)

                    # Check for startup evidence while streaming.
                    if self.session.status == TerminalStatus.STARTING:
                        if self._check_startup_success(text):
                            self.session.status = TerminalStatus.RUNNING
                            new_ports = self._extract_ports(text)
                            if new_ports:
                                self.session.ports.extend(new_ports)

                    for cb in self._output_callbacks:
                        try:
                            cb(text)
                        except Exception:
                            pass

            # Stream ended — process likely exited.
            if self.session.status in (TerminalStatus.STARTING, TerminalStatus.RUNNING):
                self.session.status = TerminalStatus.EXITED
                if self.session.process:
                    self.session.exit_code = self.session.process.returncode
        except Exception:
            if self.session.status in (TerminalStatus.STARTING, TerminalStatus.RUNNING):
                self.session.status = TerminalStatus.FAILED
        finally:
            tail = decoder.decode(b"", final=True)
            if tail:
                self.session.output.append(tail)
                for cb in self._output_callbacks:
                    try:
                        cb(tail)
                    except Exception:
                        pass

    async def stop(self, timeout: float = 10.0) -> bool:
        """Stop the persistent process and its process tree."""
        if self.session.status in (TerminalStatus.STOPPED, TerminalStatus.IDLE, TerminalStatus.EXITED, TerminalStatus.FAILED):
            return True

        self.session.status = TerminalStatus.STOPPING

        process = self.session.process
        if process:
            try:
                if sys.platform == "win32":
                    process.terminate()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except Exception:
                pass

            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                if sys.platform == "win32":
                    process.kill()
                else:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except Exception:
                        process.kill()
                await process.wait()

        self.session.status = TerminalStatus.STOPPED
        self.session.stopped_at = time.monotonic()
        self.session.exit_code = self.session.process.returncode if self.session.process else None
        self.session.process = None
        self._read_task = None

        return True

    async def restart(self, timeout: float = 10.0) -> bool:
        """Restart the persistent process."""
        if self.session.restart_count >= self.session.max_restarts:
            self.session.status = TerminalStatus.ERROR
            return False

        await self.stop(timeout=timeout)
        self.session.restart_count += 1
        return await self.start()

    def is_alive(self) -> bool:
        """Check if the process is still running."""
        if self.session.process is None:
            return False
        return self.session.process.returncode is None

    def get_status_info(self) -> dict[str, Any]:
        """Return a dictionary of session status information."""
        return {
            "session_id": self.session.session_id,
            "name": self.session.name,
            "command": self.session.command,
            "cwd": str(self.session.cwd),
            "shell": self.session.shell_type,
            "status": self.session.status.value,
            "pid": self.session.pid,
            "exit_code": self.session.exit_code,
            "ports": self.session.ports,
            "started_at": self.session.started_at,
            "stopped_at": self.session.stopped_at,
            "restart_count": self.session.restart_count,
            "output_lines": len(self.session.output),
            "is_alive": self.is_alive(),
        }


class TerminalManager:
    """Manages multiple persistent terminal sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, PersistentTerminal] = {}

    def create(
        self,
        name: str,
        command: str,
        cwd: Path,
        shell_type: str = "bash",
        auto_restart: bool = False,
        startup_timeout: float = 30.0,
    ) -> PersistentTerminal:
        """Create a new terminal session."""
        if shell_type == "bash":  # Backward-compatible default for old callers.
            from tools.core.shells import select_shell
            shell_type = select_shell(cwd=cwd).info.type.value
        session = TerminalSession(
            name=name,
            command=command,
            cwd=cwd,
            shell_type=shell_type,
            auto_restart=auto_restart,
            startup_timeout=startup_timeout,
        )
        terminal = PersistentTerminal(session)
        self._sessions[name] = terminal
        return terminal

    def get(self, name: str) -> PersistentTerminal | None:
        """Get an existing terminal session by name."""
        return self._sessions.get(name)

    def get_by_session_id(self, session_id: str) -> PersistentTerminal | None:
        """Get a terminal session by session ID."""
        for terminal in self._sessions.values():
            if terminal.session_id == session_id:
                return terminal
        return None

    def list_sessions(self) -> list[PersistentTerminal]:
        """List all terminal sessions."""
        return list(self._sessions.values())

    def remove(self, name: str) -> bool:
        """Remove a terminal session (stops it first)."""
        terminal = self._sessions.get(name)
        if terminal:
            if terminal.status == TerminalStatus.RUNNING:
                asyncio.create_task(terminal.stop())
            del self._sessions[name]
            return True
        return False

    async def stop_all(self) -> None:
        """Stop all terminal sessions."""
        for terminal in self._sessions.values():
            if terminal.status == TerminalStatus.RUNNING:
                await terminal.stop()

    def running_sessions(self) -> list[PersistentTerminal]:
        """List currently running sessions."""
        return [
            t for t in self._sessions.values()
            if t.status == TerminalStatus.RUNNING
        ]


# Global terminal manager instance.
_terminal_manager: TerminalManager | None = None


def get_terminal_manager() -> TerminalManager:
    """Get the global terminal manager (singleton)."""
    global _terminal_manager
    if _terminal_manager is None:
        _terminal_manager = TerminalManager()
    return _terminal_manager
