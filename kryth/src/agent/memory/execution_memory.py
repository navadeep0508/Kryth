"""ExecutionMemory — prevents duplicate command execution.

Before running a command, check if an identical command was already
executed with the same cwd, env, and repo state. If so, return the
cached result instead of re-executing.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CommandRecord:
    command: str
    cwd: str
    exit_code: int
    status: str  # "success" | "failed" | "running"
    output_summary: str = ""
    output_hash: str = ""
    env_hash: str = ""
    state_hash: str = ""
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    run_count: int = 1


@dataclass
class ExecutionMemory:
    commands: list[CommandRecord] = field(default_factory=list)
    _index: dict[str, CommandRecord] = field(default_factory=dict)
    max_commands: int = 200

    def record(
        self,
        command: str,
        cwd: str,
        exit_code: int,
        output_summary: str = "",
        env_hash: str = "",
        state_hash: str = "",
        duration_ms: float = 0.0,
    ) -> CommandRecord:
        record = CommandRecord(
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            status="success" if exit_code == 0 else "failed",
            output_summary=output_summary[:500],
            output_hash=hashlib.sha256(output_summary.encode()).hexdigest()[:16],
            env_hash=env_hash,
            state_hash=state_hash,
            duration_ms=duration_ms,
        )
        self.commands.append(record)
        key = self._make_key(command, cwd, state_hash)
        self._index[key] = record
        if len(self.commands) > self.max_commands:
            self.commands = self.commands[-self.max_commands:]
        return record

    def find_duplicate(
        self,
        command: str,
        cwd: str,
        state_hash: str = "",
    ) -> Optional[CommandRecord]:
        key = self._make_key(command, cwd, state_hash)
        existing = self._index.get(key)
        if existing:
            existing.run_count += 1
            return existing
        return None

    def get_recent(self, limit: int = 10) -> list[CommandRecord]:
        return self.commands[-limit:]

    def was_successful(self, command: str, cwd: str) -> bool:
        for rec in reversed(self.commands):
            if rec.command == command and rec.cwd == cwd:
                return rec.exit_code == 0
        return False

    def to_prompt_block(self, max_lines: int = 8) -> str:
        if not self.commands:
            return ""
        recent = self.commands[-max_lines:]
        lines = ["RECENT COMMAND RESULTS:"]
        for rec in recent:
            status = "✓" if rec.exit_code == 0 else "✗"
            cmd_short = rec.command[:60]
            lines.append(f"  {status} {cmd_short} (exit={rec.exit_code})")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        total = len(self.commands)
        succeeded = sum(1 for c in self.commands if c.exit_code == 0)
        return {
            "total_commands": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "unique_indexed": len(self._index),
        }

    @staticmethod
    def _make_key(command: str, cwd: str, state_hash: str) -> str:
        return hashlib.sha256(
            f"{command}||{cwd}||{state_hash}".encode()
        ).hexdigest()[:16]


class ExecutionMemoryManager:
    """Thread-safe per-session ExecutionMemory."""

    def __init__(self):
        self._sessions: dict[int, ExecutionMemory] = {}
        self._lock = threading.RLock()

    def _get(self, session_id: int) -> ExecutionMemory:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = ExecutionMemory()
            return self._sessions[session_id]

    def record(
        self,
        session_id: int,
        command: str,
        cwd: str,
        exit_code: int,
        output_summary: str = "",
        env_hash: str = "",
        state_hash: str = "",
        duration_ms: float = 0.0,
    ) -> CommandRecord:
        return self._get(session_id).record(
            command, cwd, exit_code, output_summary,
            env_hash, state_hash, duration_ms,
        )

    def find_duplicate(
        self,
        session_id: int,
        command: str,
        cwd: str,
        state_hash: str = "",
    ) -> Optional[CommandRecord]:
        return self._get(session_id).find_duplicate(command, cwd, state_hash)

    def was_successful(self, session_id: int, command: str, cwd: str) -> bool:
        return self._get(session_id).was_successful(command, cwd)

    def clear(self, session_id: int) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def get_prompt_block(self, session_id: int) -> str:
        return self._get(session_id).to_prompt_block()

    def get_stats(self, session_id: int) -> dict:
        return self._get(session_id).get_stats()
