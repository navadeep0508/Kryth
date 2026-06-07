"""Phase 12 — Interactive Terminal Support.

Handles prompts that block command execution:
  - password prompts
  - Y/N confirmation prompts
  - installer selection menus
  - license agreement prompts

Provides safe automatic responses based on a configurable policy.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class PromptMatch:
    kind: str          # "yn" | "password" | "menu" | "enter" | "custom"
    text: str          # raw prompt text
    auto_response: str = ""


# Pattern → (kind, auto_response).  Patterns are matched case-insensitively.
_PROMPT_RULES: list[tuple[re.Pattern, str, str]] = [
    # Y/N style
    (re.compile(r'\[y(?:/n)?\]\s*$', re.I),            "yn",       "y\n"),
    (re.compile(r'\(yes/no\)\s*$', re.I),               "yn",       "yes\n"),
    (re.compile(r'continue\? \[y/N\]', re.I),           "yn",       "y\n"),
    (re.compile(r'do you want to\b', re.I),             "yn",       "y\n"),
    (re.compile(r'proceed\? \(',     re.I),             "yn",       "y\n"),
    # Password
    (re.compile(r'password.*:\s*$', re.I),              "password", ""),
    (re.compile(r'enter passphrase', re.I),             "password", ""),
    # Press Enter
    (re.compile(r'press (enter|return) to continue', re.I), "enter", "\n"),
    (re.compile(r'press any key',    re.I),             "enter",    "\n"),
    # Sudo
    (re.compile(r'\[sudo\] password', re.I),            "password", ""),
    # Pip trust
    (re.compile(r'trust this package\?', re.I),         "yn",       "y\n"),
    # npm init style
    (re.compile(r'^[^\n:]+: $', re.M),                 "input",    "\n"),
]


def _match_prompt(text: str) -> Optional[PromptMatch]:
    for pattern, kind, response in _PROMPT_RULES:
        if pattern.search(text):
            return PromptMatch(kind=kind, text=text, auto_response=response)
    return None


class InteractiveHandler:
    """Detect and automatically respond to interactive prompts.

    Thread-safe. Can be attached to a PersistentShell's output stream.

    Usage:
        handler = InteractiveHandler(write_fn=shell.write)
        # Pass handler.on_line as the on_output callback to shell.execute
    """

    def __init__(
        self,
        write_fn: Callable[[str], None],
        *,
        auto_mode: bool = True,
        password: str = "",
        on_prompt: Callable[[PromptMatch], None] | None = None,
    ):
        """
        Args:
            write_fn: Function that writes a string to the shell's stdin.
            auto_mode: If True, respond automatically. If False, only detect.
            password: Password to use for password prompts (empty = skip).
            on_prompt: Optional callback when a prompt is detected.
        """
        self._write = write_fn
        self._auto = auto_mode
        self._password = password
        self._on_prompt = on_prompt
        self._buf = ""
        self._lock = threading.Lock()

    def on_line(self, line: str) -> None:
        """Feed a line of shell output. Detects and responds to prompts."""
        with self._lock:
            self._buf += line + "\n"
            # Keep only last 200 chars for matching (prompts are at end)
            if len(self._buf) > 200:
                self._buf = self._buf[-200:]

        match = _match_prompt(self._buf)
        if not match:
            return

        if self._on_prompt:
            self._on_prompt(match)

        if not self._auto:
            return

        if match.kind == "password":
            if self._password:
                self._write(self._password + "\n")
        elif match.auto_response:
            self._write(match.auto_response)

        # Clear buffer after responding
        with self._lock:
            self._buf = ""

    def set_password(self, password: str) -> None:
        self._password = password


# Module-level singleton (write_fn must be set before use)
class _LazyInteractiveHandler:
    """Placeholder until a real shell's write_fn is wired up."""

    def __init__(self) -> None:
        self._real: Optional[InteractiveHandler] = None

    def attach(self, shell) -> InteractiveHandler:
        handler = InteractiveHandler(write_fn=shell._write)
        self._real = handler
        return handler

    def on_line(self, line: str) -> None:
        if self._real:
            self._real.on_line(line)


interactive_handler = _LazyInteractiveHandler()
