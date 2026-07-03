"""MutationMemory — tracks every file mutation (write, edit, delete, multi_edit).

P0: Without this, the agent cannot answer "what did I just change?"
and will re-apply patches or enter edit loops.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MutationRecord:
    tool_name: str
    path: str
    before_hash: str = ""
    after_hash: str = ""
    diff_summary: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    content_length: int = 0
    timestamp: float = field(default_factory=time.time)
    order: int = 0

    @property
    def mutation_type(self) -> str:
        if self.tool_name == "write_file":
            return "created" if not self.before_hash else "overwritten"
        if self.tool_name == "delete_file":
            return "deleted"
        return "edited"


@dataclass
class MutationMemory:
    mutations: list[MutationRecord] = field(default_factory=list)
    _order_counter: int = 0

    def record(
        self,
        tool_name: str,
        path: str,
        before_hash: str = "",
        after_hash: str = "",
        diff_summary: str = "",
        lines_added: int = 0,
        lines_removed: int = 0,
        content_length: int = 0,
    ) -> MutationRecord:
        self._order_counter += 1
        record = MutationRecord(
            tool_name=tool_name,
            path=path,
            before_hash=before_hash,
            after_hash=after_hash,
            diff_summary=diff_summary,
            lines_added=lines_added,
            lines_removed=lines_removed,
            content_length=content_length,
            order=self._order_counter,
        )
        self.mutations.append(record)
        return record

    def get_recent(self, limit: int = 10) -> list[MutationRecord]:
        return self.mutations[-limit:]

    def get_by_path(self, path: str) -> list[MutationRecord]:
        return [m for m in self.mutations if m.path == path]

    def get_file_change_count(self, path: str) -> int:
        return sum(1 for m in self.mutations if m.path == path)

    def to_prompt_block(self, max_lines: int = 8) -> str:
        if not self.mutations:
            return ""
        recent = self.mutations[-max_lines:]
        lines = ["RECENT MUTATIONS:"]
        for m in recent:
            icon = {"created": "+", "overwritten": "~", "deleted": "-", "edited": "~"}.get(m.mutation_type, "~")
            short_path = os.path.basename(m.path)
            detail = f" [{m.lines_added}+/{m.lines_removed}-]" if m.lines_added or m.lines_removed else ""
            lines.append(f"  {icon} {short_path}{detail}  {m.diff_summary[:60]}")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        return {
            "total_mutations": len(self.mutations),
            "files_created": sum(1 for m in self.mutations if m.mutation_type == "created"),
            "files_edited": sum(1 for m in self.mutations if m.mutation_type == "edited"),
            "files_deleted": sum(1 for m in self.mutations if m.mutation_type == "deleted"),
        }


class MutationMemoryManager:
    """Thread-safe per-session MutationMemory."""

    def __init__(self):
        self._sessions: dict[int, MutationMemory] = {}
        self._lock = threading.RLock()

    def _get(self, session_id: int) -> MutationMemory:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = MutationMemory()
            return self._sessions[session_id]

    def record(
        self,
        session_id: int,
        tool_name: str,
        path: str,
        before_hash: str = "",
        after_hash: str = "",
        diff_summary: str = "",
        lines_added: int = 0,
        lines_removed: int = 0,
        content_length: int = 0,
    ) -> MutationRecord:
        return self._get(session_id).record(
            tool_name, path, before_hash, after_hash,
            diff_summary, lines_added, lines_removed, content_length,
        )

    def get_recent(self, session_id: int, limit: int = 10) -> list[MutationRecord]:
        return self._get(session_id).get_recent(limit)

    def get_by_path(self, session_id: int, path: str) -> list[MutationRecord]:
        return self._get(session_id).get_by_path(path)

    def get_file_change_count(self, session_id: int, path: str) -> int:
        return self._get(session_id).get_file_change_count(path)

    def get_prompt_block(self, session_id: int) -> str:
        return self._get(session_id).to_prompt_block()

    def clear(self, session_id: int) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def get_stats(self, session_id: int) -> dict:
        return self._get(session_id).get_stats()


def _compute_file_hash(path: str) -> str:
    """Compute SHA-256 hash of a file (first 16 chars)."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except Exception:
        return ""


def _compute_diff_summary(tool_name: str, args: dict, result: str) -> tuple[str, int, int]:
    """Extract diff summary, lines added, lines removed from tool args+result."""
    if tool_name == "write_file":
        content = args.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"wrote {len(content)} chars, {lines} lines", lines, 0

    if tool_name == "edit_file":
        old = args.get("old_string", "") or args.get("old_text", "")
        new = args.get("new_string", "") or args.get("new_text", "")
        old_lines = old.count("\n") + 1 if old else 0
        new_lines = new.count("\n") + 1 if new else 0
        added = max(0, new_lines - old_lines)
        removed = max(0, old_lines - new_lines)
        first_line = (new or old).split("\n")[0][:40].strip()
        return f'edit: "{first_line}..."', added, removed

    if tool_name == "multi_edit":
        edits = args.get("edits", [])
        count = len(edits)
        return f"multi-edit: {count} changes", 0, 0

    if tool_name == "delete_file":
        return "deleted", 0, 0

    return "", 0, 0
