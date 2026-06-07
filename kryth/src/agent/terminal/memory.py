"""Phase 16 — Terminal Memory.

Remembers across sessions:
  - successful commands and their context
  - failed commands + how they were fixed
  - project startup / build / test / deploy sequences

Persists to .kryth/terminal_memory.json
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class CommandRecord:
    command: str
    exit_code: int
    cwd: str = ""
    stack: str = ""
    context: str = ""        # git branch, venv, etc.
    timestamp: float = 0.0
    recovery_command: str = ""  # what fixed it if it failed

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


@dataclass
class WorkflowRecord:
    name: str                    # "startup", "build", "test", "deploy"
    commands: list[str] = field(default_factory=list)
    stack: str = ""
    cwd: str = ""
    success_count: int = 0
    last_used: float = 0.0


class TerminalMemory:
    """Persist and recall terminal workflows and command outcomes."""

    def __init__(self, path: str | None = None):
        self._path = path or self._default_path()
        self._lock = threading.RLock()
        self._commands: list[CommandRecord] = []
        self._workflows: dict[str, WorkflowRecord] = {}
        self._load()

    def _default_path(self) -> str:
        base = Path(os.getcwd()) / ".kryth"
        base.mkdir(exist_ok=True)
        return str(base / "terminal_memory.json")

    # ------------------------------------------------------------------
    # Recording

    def record_command(
        self,
        command: str,
        exit_code: int,
        cwd: str = "",
        stack: str = "",
        context: str = "",
        recovery_command: str = "",
    ) -> None:
        record = CommandRecord(
            command=command,
            exit_code=exit_code,
            cwd=cwd,
            stack=stack,
            context=context,
            timestamp=time.time(),
            recovery_command=recovery_command,
        )
        with self._lock:
            self._commands.append(record)
            # Keep last 500 commands
            if len(self._commands) > 500:
                self._commands = self._commands[-500:]
        self._save()

    def record_workflow(
        self,
        name: str,
        commands: list[str],
        stack: str = "",
        cwd: str = "",
    ) -> None:
        with self._lock:
            existing = self._workflows.get(name)
            if existing and existing.stack == stack:
                existing.commands = commands
                existing.success_count += 1
                existing.last_used = time.time()
            else:
                self._workflows[name] = WorkflowRecord(
                    name=name,
                    commands=commands,
                    stack=stack,
                    cwd=cwd,
                    success_count=1,
                    last_used=time.time(),
                )
        self._save()

    # ------------------------------------------------------------------
    # Recall

    def recall_workflow(self, name: str, stack: str = "") -> Optional[WorkflowRecord]:
        with self._lock:
            wf = self._workflows.get(name)
            if wf and (not stack or wf.stack == stack):
                return wf
        return None

    def recall_fix(self, failed_command: str) -> Optional[str]:
        """Return a recovery command that previously fixed this command."""
        with self._lock:
            for rec in reversed(self._commands):
                if rec.command == failed_command and rec.recovery_command:
                    return rec.recovery_command
        return None

    def successful_commands(self, cwd: str = "", limit: int = 20) -> list[str]:
        """Return recent successful commands, optionally filtered by cwd."""
        with self._lock:
            records = [
                r for r in reversed(self._commands)
                if r.succeeded and (not cwd or r.cwd == cwd)
            ]
        return [r.command for r in records[:limit]]

    def format_summary(self) -> str:
        with self._lock:
            wf_names = list(self._workflows.keys())
            recent = self._commands[-5:] if self._commands else []
        lines = [f"Workflows remembered: {', '.join(wf_names) or 'none'}"]
        if recent:
            lines.append("Recent commands:")
            for r in recent:
                mark = "✓" if r.succeeded else "✗"
                lines.append(f"  {mark} {r.command}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persistence

    def _save(self) -> None:
        try:
            with self._lock:
                data = {
                    "commands": [asdict(r) for r in self._commands],
                    "workflows": {k: asdict(v) for k, v in self._workflows.items()},
                }
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if not os.path.exists(self._path):
                return
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
            with self._lock:
                self._commands = [CommandRecord(**r) for r in data.get("commands", [])]
                self._workflows = {
                    k: WorkflowRecord(**v)
                    for k, v in data.get("workflows", {}).items()
                }
        except Exception:
            pass


# Module-level singleton
shell_memory = TerminalMemory()
