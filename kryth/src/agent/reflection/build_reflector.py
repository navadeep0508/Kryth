"""
build_reflector.py — Extracts KnowledgeLessons from build-specific terminal events
and build-related repair sequences.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionInput,
)
from agent.reflection.extractor import BaseReflector


class BuildReflector(BaseReflector):
    """Mines build-specific lessons from terminal_events and repair_events."""

    name = "build_reflector"

    _BUILD_PATTERNS = re.compile(
        r"\b(webpack|vite|cargo\s+build|mvn|gradle|make|cmake|"
        r"docker\s+build|uv\s+sync|pip\s+install|npm\s+(?:install|run|build)|"
        r"yarn\s+(?:install|build))\b",
        re.IGNORECASE,
    )

    _FRAMEWORK_MAP = {
        "webpack": "webpack",
        "vite": "vite",
        "cargo": "rust",
        "mvn": "maven",
        "gradle": "gradle",
        "make": "make",
        "cmake": "cmake",
        "docker": "docker",
        "uv": "python-uv",
        "pip": "python-pip",
        "npm": "nodejs-npm",
        "yarn": "nodejs-yarn",
    }

    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        lessons: Dict[str, KnowledgeLesson] = {}

        # Index failed build commands so we can match them against repair events.
        failed_build_cmds: Dict[str, dict] = {}
        for ev in inp.terminal_events or []:
            cmd: str = ev.get("command", "")
            if not self._BUILD_PATTERNS.search(cmd):
                continue
            exit_code: int = ev.get("exit_code", 1)
            duration: float = float(ev.get("duration_sec", 0.0))
            framework = self._detect_framework(cmd)
            slug = self._slugify(cmd, 40)

            if exit_code != 0:
                failed_build_cmds[cmd] = ev
                continue

            # Rule 1: successful build.
            key = f"build:success:{framework}:{slug}"
            lesson = self._make_lesson(
                key=key,
                kind=LessonKind.BUILD,
                scope=LessonScope.PROJECT,
                value={
                    "command": cmd,
                    "duration_sec": duration,
                    "framework": framework,
                    "note": "Build successful",
                },
                importance=0.85,
                confidence=0.85,
                reuse_potential=0.75,
            )
            lessons[key] = lesson

            # Rule 3a: slow build.
            if duration > 60.0:
                slow_key = f"build:slow:{framework}"
                lessons[slow_key] = self._make_lesson(
                    key=slow_key,
                    kind=LessonKind.BUILD,
                    scope=LessonScope.PROJECT,
                    value={
                        "command": cmd,
                        "duration_sec": duration,
                        "framework": framework,
                        "note": "Slow build — consider caching or incremental builds",
                    },
                    importance=0.7,
                    confidence=0.8,
                    reuse_potential=0.5,
                )

            # Rule 3b: fast build.
            elif duration < 10.0:
                fast_key = f"build:fast:{framework}"
                lessons[fast_key] = self._make_lesson(
                    key=fast_key,
                    kind=LessonKind.BUILD,
                    scope=LessonScope.PROJECT,
                    value={
                        "command": cmd,
                        "duration_sec": duration,
                        "framework": framework,
                        "note": "Fast build",
                    },
                    importance=0.6,
                    confidence=0.8,
                    reuse_potential=0.5,
                )

        # Rule 2: build failure followed by a successful repair.
        for ev in inp.repair_events or []:
            repair_cmd: str = ev.get("repair_command", "")
            error_type: str = ev.get("error_type", "")
            success: bool = bool(ev.get("success", False))
            if not success:
                continue

            # Determine if this repair resolved a build failure.
            matched_build_ev = self._match_failed_build(
                error_type, ev.get("error_message", ""), failed_build_cmds
            )
            if matched_build_ev is None:
                continue

            failed_cmd: str = matched_build_ev.get("command", "")
            framework = self._detect_framework(failed_cmd)
            fix_slug = self._slugify(repair_cmd, 36)

            key = f"build:repair:{framework}:{fix_slug}"
            lessons[key] = self._make_lesson(
                key=key,
                kind=LessonKind.BUILD,
                scope=LessonScope.PROJECT,
                value={
                    "framework": framework,
                    "error_type": error_type,
                    "repair": repair_cmd,
                    "result": "success",
                },
                importance=0.9,
                confidence=0.85,
                reuse_potential=0.8,
            )

        return list(lessons.values())

    # ------------------------------------------------------------------
    # Framework detection
    # ------------------------------------------------------------------

    def _detect_framework(self, command: str) -> str:
        """Return a canonical framework name from a build command string."""
        cmd_lower = command.lower()
        for token, framework in self._FRAMEWORK_MAP.items():
            if token in cmd_lower:
                return framework
        return "unknown"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_failed_build(
        self,
        error_type: str,
        error_message: str,
        failed_builds: Dict[str, dict],
    ) -> Optional[dict]:
        """
        Heuristically link a repair event to a failed build terminal event.

        Matches when the error_type/message contains a build-related keyword
        and there is at least one recorded failed build command.
        """
        if not failed_builds:
            return None

        combined = f"{error_type} {error_message}".lower()
        build_keywords = (
            "build", "compile", "make", "cmake", "mvn", "gradle",
            "webpack", "vite", "cargo", "docker", "npm", "yarn", "pip", "uv",
        )
        if not any(kw in combined for kw in build_keywords):
            return None

        # Return the first (and typically only) failed build event.
        return next(iter(failed_builds.values()))
