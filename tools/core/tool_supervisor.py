"""
KRYTH Tool Selection Supervisor.

Prefers native tools (read/write/search/directory) over shell commands where a
native equivalent exists and is clearly better. Provides command classification,
native alternative recommendations, and integration points for the Bash tool to
warn or suggest native tools before spawning a subprocess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CommandCategory(str, Enum):
    """What a shell command is trying to accomplish."""

    READ_FILE = "read_file"
    LIST_DIRECTORY = "list_directory"
    SEARCH_CONTENT = "search_content"
    FIND_FILES = "find_files"
    WRITE_FILE = "write_file"
    EDIT_FILE = "edit_file"
    RUN_COMMAND = "run_command"
    OTHER = "other"


@dataclass
class NativeToolRecommendation:
    """A recommendation to use a native tool instead of a shell command."""

    category: CommandCategory
    tool_name: str  # e.g. "read", "grep", "glob", "list_directories", "write", "edit"
    reason: str  # human-readable explanation
    confidence: float  # 0.0–1.0, how confident we are this is a safe replacement
    params_hint: dict[str, Any]  # suggested parameters for the native tool
    can_replace: bool  # True if the native tool can fully replace the shell command


class ToolSelectionSupervisor:
    """Decides whether a shell command should be replaced with a native tool."""

    # -- shell command → category patterns -----------------------------------

    _READ_FILE_PATTERNS = [
        (r"^\s*cat\s+\S+", CommandCategory.READ_FILE),
        (r"^\s*type\s+\S+", CommandCategory.READ_FILE),
        (r"^\s*head\s+", CommandCategory.READ_FILE),
        (r"^\s*tail\s+", CommandCategory.READ_FILE),
        (r"^\s*less\s+", CommandCategory.READ_FILE),
        (r"^\s*more\s+", CommandCategory.READ_FILE),
    ]

    _LIST_DIR_PATTERNS = [
        (r"^\s*ls\s*", CommandCategory.LIST_DIRECTORY),
        (r"^\s*dir\s*", CommandCategory.LIST_DIRECTORY),
    ]

    _SEARCH_CONTENT_PATTERNS = [
        (r"^\s*grep\s+", CommandCategory.SEARCH_CONTENT),
        (r"^\s*rg\s+", CommandCategory.SEARCH_CONTENT),
        (r"^\s*ag\s+", CommandCategory.SEARCH_CONTENT),
        (r"^\s*ack\s+", CommandCategory.SEARCH_CONTENT),
    ]

    _FIND_FILES_PATTERNS = [
        (r"^\s*find\s+", CommandCategory.FIND_FILES),
        (r"^\s*where\s+", CommandCategory.FIND_FILES),
        (r"^\s*which\s+", CommandCategory.FIND_FILES),
    ]

    _WRITE_FILE_PATTERNS = [
        (r"^\s*echo\s+.+\s*>\s*\S+", CommandCategory.WRITE_FILE),
        (r"^\s*echo\s+.+\s*>>\s*\S+", CommandCategory.WRITE_FILE),
        (r"^\s*printf\s+.+\s*>\s*\S+", CommandCategory.WRITE_FILE),
        (r"^\s*touch\s+\S+", CommandCategory.WRITE_FILE),
    ]

    _EDIT_FILE_PATTERNS = [
        (r"^\s*sed\s+.*-i\s+", CommandCategory.EDIT_FILE),
        (r"^\s*perl\s+.*-i\s+", CommandCategory.EDIT_FILE),
    ]

    def __init__(self) -> None:
        self._native_tools: dict[str, str] = {
            "read": "Read file contents with line numbers",
            "write": "Write or overwrite a file",
            "edit": "Apply targeted edits to a file",
            "grep": "Search file content with regex",
            "glob": "Find files matching a pattern",
            "list_directories": "List directory contents",
        }

    # -- public API ----------------------------------------------------------

    def classify(self, command: str) -> CommandCategory:
        """Classify what a shell command is trying to accomplish."""
        lowered = command.strip()

        for pattern, category in self._READ_FILE_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return category

        for pattern, category in self._LIST_DIR_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return category

        for pattern, category in self._SEARCH_CONTENT_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return category

        for pattern, category in self._FIND_FILES_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return category

        for pattern, category in self._WRITE_FILE_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return category

        for pattern, category in self._EDIT_FILE_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return category

        return CommandCategory.OTHER

    def recommend(self, command: str) -> NativeToolRecommendation | None:
        """Return a native tool recommendation for *command*, or None if none
        applies."""
        category = self.classify(command)

        if category == CommandCategory.READ_FILE:
            path = self._extract_path(command)
            return NativeToolRecommendation(
                category=category,
                tool_name="read",
                reason=(
                    "Use the read tool instead of cat/type/head/tail — it returns "
                    "line-numbered text, handles encoding safely, and avoids "
                    "spawning a subprocess."
                ),
                confidence=0.95,
                params_hint={"path": path or "."},
                can_replace=True,
            )

        if category == CommandCategory.LIST_DIRECTORY:
            path = self._extract_path(command) or "."
            return NativeToolRecommendation(
                category=category,
                tool_name="list_directories",
                reason=(
                    "Use the list_directories tool instead of ls/dir — it returns "
                    "sorted entries with file/dir distinction and supports "
                    "path traversal safety checks."
                ),
                confidence=0.95,
                params_hint={"path": path},
                can_replace=True,
            )

        if category == CommandCategory.SEARCH_CONTENT:
            pattern = self._extract_grep_pattern(command)
            path = self._extract_path(command) or "."
            return NativeToolRecommendation(
                category=category,
                tool_name="grep",
                reason=(
                    "Use the grep tool instead of grep/rg/ag — it searches file "
                    "content with regex, skips binary files and common ignore "
                    "directories, and returns matches with file paths and line "
                    "numbers."
                ),
                confidence=0.9,
                params_hint={"pattern": pattern or "", "path": path},
                can_replace=True,
            )

        if category == CommandCategory.FIND_FILES:
            pattern = self._extract_find_pattern(command)
            path = self._extract_path(command) or "."
            return NativeToolRecommendation(
                category=category,
                tool_name="glob",
                reason=(
                    "Use the glob tool instead of find/where/which — it supports "
                    "recursive ** patterns, returns file paths only, and respects "
                    "path traversal safety checks."
                ),
                confidence=0.85,
                params_hint={"pattern": pattern or "*", "path": path},
                can_replace=True,
            )

        if category == CommandCategory.WRITE_FILE:
            path = self._extract_path(command)
            return NativeToolRecommendation(
                category=category,
                tool_name="write",
                reason=(
                    "Use the write tool instead of echo/printf redirection — it "
                    "ensures parent directories exist, validates paths, and avoids "
                    "shell escaping issues."
                ),
                confidence=0.8,
                params_hint={"path": path or ""},
                can_replace=True,
            )

        if category == CommandCategory.EDIT_FILE:
            path = self._extract_path(command)
            return NativeToolRecommendation(
                category=category,
                tool_name="edit",
                reason=(
                    "Use the edit tool instead of sed -i/perl -i — it performs "
                    "exact string replacement with validation and read-back "
                    "verification."
                ),
                confidence=0.75,
                params_hint={"path": path or ""},
                can_replace=True,
            )

        return None

    def should_prefer_native(self, command: str) -> bool:
        """Return True when a native tool can clearly replace *command*."""
        rec = self.recommend(command)
        return rec is not None and rec.can_replace and rec.confidence >= 0.8

    def format_recommendation(self, command: str) -> str | None:
        """Return a human-readable recommendation string, or None."""
        rec = self.recommend(command)
        if rec is None:
            return None

        lines = [
            f"Consider using the `{rec.tool_name}` tool instead of running this shell command.",
            f"  Reason: {rec.reason}",
            f"  Confidence: {rec.confidence:.0%}",
        ]
        if rec.params_hint:
            lines.append(f"  Suggested params: {rec.params_hint}")
        return "\n".join(lines)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _extract_path(command: str) -> str | None:
        """Best-effort extraction of a file path from a shell command."""
        # Remove common command prefixes and flags, then take the first
        # token that looks like a path.
        stripped = command.strip()
        for prefix in ("cat ", "type ", "head ", "tail ", "less ", "more ",
                        "ls ", "dir ", "grep ", "rg ", "ag ", "ack ",
                        "find ", "where ", "which ", "echo ", "printf ",
                        "touch ", "sed ", "perl "):
            if stripped.lower().startswith(prefix):
                stripped = stripped[len(prefix):]
                break

        # Take the first non-flag token.
        for token in stripped.split():
            if not token.startswith("-") and token:
                # Strip quotes.
                return token.strip("\"'")
        return None

    @staticmethod
    def _extract_grep_pattern(command: str) -> str | None:
        """Extract the search pattern from a grep-like command."""
        stripped = command.strip()
        for prefix in ("grep ", "rg ", "ag ", "ack "):
            if stripped.lower().startswith(prefix):
                rest = stripped[len(prefix):].strip()
                # Skip flags.
                tokens = rest.split()
                for i, token in enumerate(tokens):
                    if not token.startswith("-"):
                        return token.strip("\"'")
                break
        return None

    @staticmethod
    def _extract_find_pattern(command: str) -> str | None:
        """Extract the search pattern from a find-like command."""
        stripped = command.strip()
        for prefix in ("find ", "where ", "which "):
            if stripped.lower().startswith(prefix):
                rest = stripped[len(prefix):].strip()
                tokens = rest.split()
                for i, token in enumerate(tokens):
                    if not token.startswith("-") and not token.startswith("("):
                        return token.strip("\"'")
                break
        return None


# Singleton instance for convenience.
_supervisor: ToolSelectionSupervisor | None = None


def get_tool_supervisor() -> ToolSelectionSupervisor:
    """Return the shared ToolSelectionSupervisor instance."""
    global _supervisor
    if _supervisor is None:
        _supervisor = ToolSelectionSupervisor()
    return _supervisor


def classify_command(command: str) -> CommandCategory:
    """Convenience: classify a shell command's intent."""
    return get_tool_supervisor().classify(command)


def recommend_native_tool(command: str) -> NativeToolRecommendation | None:
    """Convenience: recommend a native tool for a shell command."""
    return get_tool_supervisor().recommend(command)


def should_prefer_native(command: str) -> bool:
    """Convenience: True when a native tool can clearly replace *command*."""
    return get_tool_supervisor().should_prefer_native(command)


__all__ = [
    "CommandCategory",
    "NativeToolRecommendation",
    "ToolSelectionSupervisor",
    "get_tool_supervisor",
    "classify_command",
    "recommend_native_tool",
    "should_prefer_native",
]
