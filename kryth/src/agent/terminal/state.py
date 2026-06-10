"""Phase 2 — Terminal State Manager.

Tracks and exposes shared environment state to all agents:
  - current working directory
  - active git branch + dirty status
  - active Python virtualenv
  - active Node version
  - running servers (detected by open ports)
  - docker containers
  - open ports
  - key environment variables

State is refreshed lazily (TTL-based) so repeated reads are cheap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


_TTL = 5.0  # seconds before a cached value is considered stale


@dataclass
class GitState:
    branch: str = ""
    dirty: bool = False
    untracked: int = 0
    ahead: int = 0
    behind: int = 0


@dataclass
class PythonEnv:
    venv_path: str = ""
    venv_name: str = ""
    python_version: str = ""


@dataclass
class NodeEnv:
    node_version: str = ""
    npm_version: str = ""
    package_manager: str = ""


@dataclass
class RunningServer:
    port: int = 0
    pid: int = 0
    command: str = ""


@dataclass
class DockerContainer:
    container_id: str = ""
    name: str = ""
    image: str = ""
    status: str = ""
    ports: list[str] = field(default_factory=list)


@dataclass
class TerminalState:
    cwd: str = ""
    git: Optional[GitState] = None
    python_env: Optional[PythonEnv] = None
    node_env: Optional[NodeEnv] = None
    open_ports: list[int] = field(default_factory=list)
    running_servers: list[RunningServer] = field(default_factory=list)
    docker_containers: list[DockerContainer] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    last_updated: float = 0.0


def _run(args: list[str], cwd: str | None = None, timeout: float = 3.0) -> str:
    try:
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=cwd,
        )
        return r.stdout.strip()
    except Exception:
        return ""


class TerminalStateManager:
    """Singleton that tracks shared terminal state for all agents."""

    def __init__(self) -> None:
        self._state = TerminalState()
        self._lock = threading.RLock()
        self._last_refresh: float = 0.0

    def snapshot(self, force: bool = False) -> TerminalState:
        """Return current state, refreshing if TTL expired."""
        with self._lock:
            now = time.monotonic()
            if force or (now - self._last_refresh) > _TTL:
                self._refresh()
                self._last_refresh = now
            return self._state

    def _refresh(self) -> None:
        state = TerminalState()
        state.cwd = os.getcwd()
        state.last_updated = time.time()

        state.git = self._get_git(state.cwd)
        state.python_env = self._get_python_env()
        state.node_env = self._get_node_env()
        state.open_ports = self._get_open_ports()
        state.running_servers = self._get_running_servers(state.open_ports)
        state.docker_containers = self._get_docker_containers()
        state.env_vars = self._get_env_vars()

        self._state = state

    def _get_git(self, cwd: str) -> GitState | None:
        if not shutil.which("git"):
            return None
        branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        if not branch or branch == "fatal:":
            return None

        status_out = _run(["git", "status", "--porcelain"], cwd=cwd)
        dirty = bool(status_out.strip())
        untracked = sum(1 for l in status_out.splitlines() if l.startswith("??"))

        ahead = behind = 0
        ab_out = _run(
            ["git", "rev-list", "--left-right", "--count", "@{u}...HEAD"], cwd=cwd
        )
        if ab_out:
            parts = ab_out.split()
            if len(parts) == 2:
                try:
                    behind, ahead = int(parts[0]), int(parts[1])
                except ValueError:
                    pass

        return GitState(
            branch=branch, dirty=dirty, untracked=untracked,
            ahead=ahead, behind=behind
        )

    def _get_python_env(self) -> PythonEnv:
        env = PythonEnv()
        venv = os.environ.get("VIRTUAL_ENV", "")
        if venv:
            env.venv_path = venv
            env.venv_name = os.path.basename(venv)

        py = shutil.which("python") or shutil.which("python3")
        if py:
            ver = _run([py, "--version"])
            env.python_version = ver.replace("Python ", "")

        return env

    def _get_node_env(self) -> NodeEnv:
        env = NodeEnv()
        node = shutil.which("node")
        if node:
            env.node_version = _run([node, "--version"])
        npm = shutil.which("npm")
        if npm:
            env.npm_version = _run([npm, "--version"])
            env.package_manager = "npm"
        if shutil.which("pnpm"):
            env.package_manager = "pnpm"
        elif shutil.which("yarn"):
            env.package_manager = "yarn"
        return env

    def _get_open_ports(self) -> list[int]:
        """Return list of locally listening TCP ports."""
        ports: list[int] = []
        try:
            import psutil  # type: ignore
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == "LISTEN" and conn.laddr:
                    ports.append(conn.laddr.port)
        except ImportError:
            # Fallback: parse netstat output
            if sys.platform.startswith("win"):
                out = _run(["netstat", "-an"], timeout=5.0)
            else:
                out = _run(["ss", "-tlnp"], timeout=5.0) or _run(
                    ["netstat", "-tlnp"], timeout=5.0
                )
            for line in out.splitlines():
                parts = line.split()
                for p in parts:
                    if ":" in p:
                        try:
                            port = int(p.rsplit(":", 1)[-1])
                            if 1 <= port <= 65535:
                                ports.append(port)
                        except ValueError:
                            pass
        return sorted(set(ports))

    def _get_running_servers(self, ports: list[int]) -> list[RunningServer]:
        servers: list[RunningServer] = []
        try:
            import psutil  # type: ignore
            for conn in psutil.net_connections(kind="inet"):
                if conn.status == "LISTEN" and conn.laddr and conn.pid:
                    try:
                        proc = psutil.Process(conn.pid)
                        cmd = " ".join(proc.cmdline()[:4])
                    except Exception:
                        cmd = f"pid={conn.pid}"
                    servers.append(RunningServer(
                        port=conn.laddr.port,
                        pid=conn.pid,
                        command=cmd,
                    ))
        except ImportError:
            pass
        return servers

    def _get_docker_containers(self) -> list[DockerContainer]:
        if not shutil.which("docker"):
            return []
        out = _run(
            ["docker", "ps", "--format",
             "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"],
            timeout=4.0,
        )
        containers: list[DockerContainer] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 5:
                containers.append(DockerContainer(
                    container_id=parts[0],
                    name=parts[1],
                    image=parts[2],
                    status=parts[3],
                    ports=parts[4].split(",") if parts[4] else [],
                ))
        return containers

    def _get_env_vars(self) -> dict[str, str]:
        keys = (
            "PATH", "VIRTUAL_ENV", "NODE_ENV", "PYTHONPATH",
            "HOME", "USER", "SHELL", "TERM", "LANG",
        )
        return {k: os.environ[k] for k in keys if k in os.environ}

    def format_summary(self) -> str:
        """Return a concise text summary of current state."""
        state = self.snapshot()
        lines = [f"cwd: {state.cwd}"]

        if state.git:
            g = state.git
            dirty_marker = " *" if g.dirty else ""
            lines.append(f"git: {g.branch}{dirty_marker}")

        if state.python_env and state.python_env.venv_name:
            lines.append(f"venv: {state.python_env.venv_name} ({state.python_env.python_version})")

        if state.node_env and state.node_env.node_version:
            lines.append(f"node: {state.node_env.node_version} ({state.node_env.package_manager})")

        if state.running_servers:
            server_list = ", ".join(
                f":{s.port}" for s in state.running_servers[:5]
            )
            lines.append(f"servers: {server_list}")

        if state.docker_containers:
            names = ", ".join(c.name for c in state.docker_containers[:3])
            lines.append(f"docker: {names}")

        return "\n".join(lines)


# Module-level singleton
terminal_state = TerminalStateManager()
