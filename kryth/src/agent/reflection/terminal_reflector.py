"""
terminal_reflector.py — Extracts KnowledgeLessons from terminal command execution events.
"""
from __future__ import annotations

import re
from typing import Dict, List

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionInput,
)
from agent.reflection.extractor import BaseReflector


class TerminalReflector(BaseReflector):
    """Mines lessons from ReflectionInput.terminal_events."""

    name = "terminal_reflector"

    _TEST_RE = re.compile(
        r"\b(pytest|jest|cargo\s+test|go\s+test|rspec|mocha)\b", re.IGNORECASE
    )
    _BUILD_RE = re.compile(
        r"\b(make|cmake|cargo\s+build|mvn|gradle|docker\s+build)\b", re.IGNORECASE
    )
    _GIT_RE = re.compile(r"^git\s+(commit|push|merge)\b", re.IGNORECASE)
    _INSTALL_RE = re.compile(
        r"\b(pip|npm|yarn|uv)\s+install\b", re.IGNORECASE
    )
    _DEPLOY_RE = re.compile(
        r"\b(deploy|kubectl\s+apply|helm\s+install|terraform\s+apply)\b", re.IGNORECASE
    )

    _TRIVIAL_CMDS = frozenset({"ls", "pwd", "echo", "cat"})

    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        events = inp.terminal_events or []

        # Pre-count successful executions per command for the repetition rule.
        success_counts: Dict[str, int] = {}
        for ev in events:
            if not self._is_noise(ev) and ev.get("exit_code", 1) == 0:
                cmd = ev.get("command", "")
                success_counts[cmd] = success_counts.get(cmd, 0) + 1

        lessons: Dict[str, KnowledgeLesson] = {}

        for ev in events:
            if self._is_noise(ev):
                continue

            cmd: str = ev.get("command", "")
            exit_code: int = ev.get("exit_code", 1)
            duration: float = float(ev.get("duration_sec", 0.0))
            slug = self._command_slug(cmd)

            # Rule 4: slow command (any exit code).
            if duration > 30.0:
                key = f"command:slow:{slug}"
                lessons[key] = self._make_lesson(
                    key=key,
                    kind=LessonKind.COMMAND,
                    scope=LessonScope.EXECUTION,
                    value={
                        "command": cmd,
                        "duration_sec": duration,
                        "note": "Consider caching or parallelizing",
                    },
                    importance=0.7,
                    confidence=0.7,
                    reuse_potential=0.5,
                )

            # Rule 5: parsed errors with a suggested fix.
            for err in ev.get("parsed_errors", []):
                if not err.get("suggested_fix"):
                    continue
                et_slug = self._slugify(err.get("error_type", "unknown"), 32)
                key = f"terminal:error:{et_slug}"
                lessons[key] = self._make_lesson(
                    key=key,
                    kind=LessonKind.REPAIR,
                    scope=LessonScope.EXECUTION,
                    value={
                        "error_type": err.get("error_type"),
                        "suggested_fix": err.get("suggested_fix"),
                        "error_kind": err.get("kind"),
                        "location": err.get("location"),
                    },
                    importance=0.85,
                    confidence=0.75,
                    reuse_potential=0.8,
                )

            if exit_code != 0:
                continue

            # ---------- successful command rules ----------
            run_count = success_counts.get(cmd, 1)
            repeated = run_count >= 2
            cmd_class = self._classify_command(cmd)

            if cmd_class == "test":
                # Rule 6: test command success.
                key = f"command:success:{slug}"
                lessons[key] = self._make_lesson(
                    key=key,
                    kind=LessonKind.TEST,
                    scope=LessonScope.PROJECT,
                    value={
                        "command": cmd,
                        "duration_sec": duration,
                        "note": "Test suite passing",
                    },
                    importance=0.8,
                    confidence=0.8,
                    reuse_potential=0.7,
                )

            elif cmd_class == "build":
                # Rule 7: build command success.
                key = f"command:success:{slug}"
                lessons[key] = self._make_lesson(
                    key=key,
                    kind=LessonKind.BUILD,
                    scope=LessonScope.PROJECT,
                    value={"command": cmd, "duration_sec": duration},
                    importance=0.8,
                    confidence=0.8,
                    reuse_potential=0.7,
                )

            elif cmd_class == "git":
                # Rule 8: git workflow operation.
                key = f"command:success:{slug}"
                lessons[key] = self._make_lesson(
                    key=key,
                    kind=LessonKind.WORKFLOW,
                    scope=LessonScope.PROJECT,
                    value={"command": cmd, "note": "Version control workflow"},
                    importance=0.65,
                    confidence=0.8,
                    reuse_potential=0.5,
                )

            else:
                # Rules 1 & 2: generic command.
                key = f"command:success:{slug}"
                if repeated:
                    # Rule 2: repeated — elevate to project scope.
                    lessons[key] = self._make_lesson(
                        key=key,
                        kind=LessonKind.COMMAND,
                        scope=LessonScope.PROJECT,
                        value={
                            "command": cmd,
                            "duration_sec": duration,
                            "run_count": run_count,
                        },
                        importance=0.9,
                        confidence=0.8,
                        reuse_potential=0.8,
                    )
                else:
                    # Rule 1: single successful run.
                    lessons[key] = self._make_lesson(
                        key=key,
                        kind=LessonKind.COMMAND,
                        scope=LessonScope.EXECUTION,
                        value={
                            "command": cmd,
                            "duration_sec": duration,
                            "run_count": run_count,
                        },
                        importance=0.7,
                        confidence=0.8,
                        reuse_potential=0.6,
                    )

            # Rule 3: fast command — additional lesson, separate key.
            if duration < 2.0:
                fast_key = f"command:fast:{slug}"
                lessons[fast_key] = self._make_lesson(
                    key=fast_key,
                    kind=LessonKind.COMMAND,
                    scope=LessonScope.EXECUTION,
                    value={
                        "command": cmd,
                        "duration_sec": duration,
                        "note": "Quick operation",
                    },
                    importance=0.6,
                    confidence=0.8,
                    reuse_potential=0.5,
                )

        return list(lessons.values())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _classify_command(self, command: str) -> str:
        """Classify a shell command into a broad category string."""
        if self._TEST_RE.search(command):
            return "test"
        if self._BUILD_RE.search(command):
            return "build"
        if self._GIT_RE.match(command):
            return "git"
        if self._INSTALL_RE.search(command):
            return "install"
        if self._DEPLOY_RE.search(command):
            return "deploy"
        return "other"

    def _is_noise(self, event: dict) -> bool:
        """Return True when the event carries no actionable signal."""
        cmd: str = event.get("command", "").strip()
        if not cmd:
            return True

        exit_code: int = event.get("exit_code", 0)
        output: str = event.get("output", "")
        duration: float = float(event.get("duration_sec", 0.0))

        # Long raw failure output — too noisy to extract lessons from.
        if len(output) > 5000 and exit_code != 0:
            return True

        # Trivial read-only commands.
        first_word = cmd.split()[0]
        if first_word in self._TRIVIAL_CMDS:
            return True

        # Sub-millisecond execution — almost certainly trivial.
        if duration < 0.01:
            return True

        return False

    def _command_slug(self, command: str) -> str:
        """Stable short identifier for a command string."""
        return self._slugify(command, 40)
