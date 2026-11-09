"""
Long-running command detection for KRYTH.

Detects commands that should run as persistent background sessions
rather than blocking foreground executions (e.g., dev servers).
Combines command keyword matching with project detection metadata.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.core.project import detect_project


@dataclass
class LongRunningInfo:
    """Information about a detected long-running command."""

    is_long_running: bool
    command_type: str | None = None  # e.g. "fastapi", "react", "generic"
    session_name: str | None = None
    suggested_name: str | None = None
    ports: list[int] = field(default_factory=list)
    description: str | None = None


# Command patterns that indicate long-running processes.
# Ordered from most specific to least specific.
_LONG_RUNNING_PATTERNS = [
    # Python web servers
    r"\buvicorn\b",
    r"\bgunicorn\b",
    r"\bwaitress\b",
    r"\bflask\s+run\b",
    r"\bdjango\s+admin\s+runserver\b",
    r"\bmanage\.py\s+runserver\b",
    # Node.js dev servers
    r"\bnpm\s+run\s+dev\b",
    r"\bnpm\s+start\b",
    r"\bpnpm\s+(dev|start)\b",
    r"\byarn\s+(dev|start)\b",
    r"\bnpx\s+(next|vite|nuxt)\b",
    r"\bvite\b",
    r"\bnext\s+dev\b",
    r"\bnuxt\s+dev\b",
    # Generic / infrastructure
    r"\bnode\s+.+\.js\b",
    r"\bpython\s+.+\.py\b",
    r"\bdocker-compose\s+up\b",
    r"\bdocker\s+compose\s+up\b",
]


def _is_long_running_command(command: str) -> bool:
    """Check if a command matches known long-running patterns."""
    lowered = command.lower()
    for pattern in _LONG_RUNNING_PATTERNS:
        if re.search(pattern, lowered):
            return True
    return False


def _command_type(command: str) -> str | None:
    """Infer the type of long-running command from its text."""
    lowered = command.lower()
    if re.search(r"\buvicorn\b", lowered):
        return "fastapi"
    if re.search(r"\bgunicorn\b", lowered):
        return "wsgi"
    if re.search(r"\bflask\s+run\b", lowered):
        return "flask"
    if re.search(r"\bdjango\s+admin\s+runserver\b|\bmanage\.py\s+runserver\b", lowered):
        return "django"
    if re.search(r"\bnext\s+dev\b", lowered):
        return "nextjs"
    if re.search(r"\bvite\b", lowered):
        return "vite"
    if re.search(r"\bnpm\s+run\s+dev\b", lowered):
        return "node-dev"
    if re.search(r"\bnpm\s+start\b", lowered):
        return "node-start"
    if re.search(r"\bpnpm\s+(dev|start)\b", lowered):
        return "pnpm-dev"
    if re.search(r"\byarn\s+(dev|start)\b", lowered):
        return "yarn-dev"
    if re.search(r"\bdocker-compose\s+up\b|\bdocker\s+compose\s+up\b", lowered):
        return "docker-compose"
    if re.search(r"\bnode\s+.+\.js\b", lowered):
        return "node-script"
    if re.search(r"\bpython\s+.+\.py\b", lowered):
        return "python-script"
    return "generic"


def _expected_ports(command_type: str | None) -> list[int]:
    """Return expected default ports for a command type."""
    port_map = {
        "fastapi": [8000],
        "flask": [5000],
        "django": [8000],
        "nextjs": [3000],
        "vite": [5173],
        "react": [3000],
        "node-dev": [3000],
        "node-start": [3000],
        "pnpm-dev": [3000],
        "yarn-dev": [3000],
        "wsgi": [8000],
        "docker-compose": [],
    }
    return list(port_map.get(command_type, []))


def _session_name(command: str, cwd: Path) -> str:
    """Generate a stable session name from command and working directory."""
    key = f"{cwd}:{command}"
    short = hashlib.md5(key.encode()).hexdigest()[:8]
    first_word = command.strip().split()[0] if command.strip() else "cmd"
    # Sanitize first word for use in a name.
    safe = re.sub(r"[^a-z0-9]+", "-", first_word.lower()).strip("-")
    return f"{safe}-{short}"


def detect_long_running(
    command: str,
    cwd: Path | str | None = None,
) -> LongRunningInfo:
    """Detect if a command is long-running and should be run as a persistent session.

    Uses both command keyword matching and project detection metadata.
    Returns LongRunningInfo with is_long_running=False when no match is found.
    """
    if not _is_long_running_command(command):
        return LongRunningInfo(is_long_running=False)

    cmd_type = _command_type(command)
    cwd_path = Path(cwd) if cwd else Path.cwd()
    name = _session_name(command, cwd_path)

    ports = _expected_ports(cmd_type)
    description = f"Long-running: {command}"

    # Enhance with project detection metadata.
    try:
        plan = detect_project(cwd_path)
        if plan.project_type != "unknown":
            description = f"{plan.project_type} service: {command}"
            for svc in plan.services:
                if svc.start_command and svc.start_command in command:
                    for ep in svc.endpoints:
                        if ep.port and ep.port not in ports:
                            ports.append(ep.port)
    except Exception:
        pass

    return LongRunningInfo(
        is_long_running=True,
        command_type=cmd_type,
        session_name=name,
        suggested_name=name,
        ports=ports,
        description=description,
    )


__all__ = [
    "LongRunningInfo",
    "detect_long_running",
]
