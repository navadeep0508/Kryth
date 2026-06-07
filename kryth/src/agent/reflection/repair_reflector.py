"""
repair_reflector.py — Extracts KnowledgeLessons from repair events and implicit
failure→fix sequences found in tool_calls.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionInput,
)
from agent.reflection.extractor import BaseReflector


class RepairReflector(BaseReflector):
    """Mines repair knowledge from explicit repair_events and implicit tool_call patterns."""

    name = "repair_reflector"

    def __init__(self, project_hash: str = "") -> None:
        super().__init__(project_hash)
        self._error_re = self._error_indicators()
        self._fix_re = self._fix_indicators()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        lessons: Dict[str, KnowledgeLesson] = {}

        for lesson in self._extract_from_repair_events(inp.repair_events or []):
            lessons[lesson.key] = lesson

        for lesson in self._extract_from_tool_calls(inp.tool_calls or []):
            # Don't overwrite an explicit repair lesson with an implicit one.
            if lesson.key not in lessons:
                lessons[lesson.key] = lesson

        return list(lessons.values())

    # ------------------------------------------------------------------
    # Explicit repair_events
    # ------------------------------------------------------------------

    def _extract_from_repair_events(self, events: list) -> List[KnowledgeLesson]:
        lessons: List[KnowledgeLesson] = []

        for ev in events:
            error_type: str = ev.get("error_type", "")
            error_message: str = ev.get("error_message", "")
            repair_cmd: str = ev.get("repair_command", "")
            success: bool = bool(ev.get("success", False))
            attempts: int = int(ev.get("attempts", 1))
            agent_id: str = ev.get("agent_id", "")

            error_class = self._classify_error(error_type, error_message)
            fix_slug = self._fix_slug(repair_cmd)

            if success:
                # Rule 3: single-attempt → fast repair (highest importance).
                if attempts == 1:
                    importance = 0.95
                # Rule 4: multi-attempt → potentially fragile.
                elif attempts > 2:
                    importance = 0.7
                else:
                    importance = 0.9  # Rule 1 base

                confidence = 1.0 if attempts == 1 else 0.8

                key = f"repair:{error_class}:{fix_slug}"
                value: dict = {
                    "error_type": error_type,
                    "error_message": error_message[:300],
                    "repair_command": repair_cmd,
                    "attempts": attempts,
                    "confidence": confidence,
                }
                metadata: dict = {}
                if attempts > 2:
                    metadata["warning"] = "Repair required multiple attempts — may be fragile"

                lesson = self._make_lesson(
                    key=key,
                    kind=LessonKind.REPAIR,
                    scope=LessonScope.EXECUTION,
                    value=value,
                    importance=importance,
                    confidence=confidence,
                    reuse_potential=0.85,
                    metadata=metadata,
                )
                if agent_id:
                    lesson.agent_id = agent_id
                lessons.append(lesson)

            else:
                # Rule 2: exhausted all attempts without success.
                key = f"repair:unsolved:{error_class}"
                lessons.append(self._make_lesson(
                    key=key,
                    kind=LessonKind.REPAIR,
                    scope=LessonScope.SESSION,
                    value={
                        "error_type": error_type,
                        "attempts": attempts,
                        "note": "Could not repair automatically",
                    },
                    importance=0.6,
                    confidence=0.5,
                    reuse_potential=0.3,
                ))

        return lessons

    # ------------------------------------------------------------------
    # Implicit repair detection from tool_calls
    # ------------------------------------------------------------------

    def _extract_from_tool_calls(self, calls: list) -> List[KnowledgeLesson]:
        """Detect failure→success pairs within a 3-step window."""
        lessons: List[KnowledgeLesson] = []
        n = len(calls)

        for i in range(n - 1):
            failed = calls[i]
            if bool(failed.get("success", True)):
                continue  # not a failure

            err_text = self._result_text(failed)
            if not self._error_re.search(err_text):
                continue

            # Look up to 3 steps ahead for a successful fix.
            for j in range(i + 1, min(i + 4, n)):
                fix_call = calls[j]
                if not bool(fix_call.get("success", True)):
                    continue

                fix_text = self._result_text(fix_call)
                if not self._fix_re.search(fix_text) and j != i + 1:
                    continue

                error_class = self._classify_error("", err_text)
                fix_cmd = self._first_arg(fix_call.get("args") or {}) or str(
                    fix_call.get("tool") or fix_call.get("name") or ""
                )
                fix_slug = self._fix_slug(fix_cmd)
                key = f"repair:implicit:{error_class}:{fix_slug}"

                lessons.append(self._make_lesson(
                    key=key,
                    kind=LessonKind.REPAIR,
                    scope=LessonScope.EXECUTION,
                    value={
                        "error_class": error_class,
                        "error_snippet": err_text[:200],
                        "fix_command": fix_cmd,
                        "steps_apart": j - i,
                    },
                    importance=0.75,
                    confidence=0.65,
                    reuse_potential=0.6,
                    metadata={"implicit": True},
                ))
                break  # one repair per failure

        return lessons

    # ------------------------------------------------------------------
    # Error classification
    # ------------------------------------------------------------------

    def _classify_error(self, error_type: str, error_message: str) -> str:
        """Map error_type + message text to a short category slug."""
        combined = f"{error_type} {error_message}".lower()

        if "modulenotfounderror" in error_type.lower() or "importerror" in error_type.lower() or (
            "import" in combined and ("error" in combined or "no module" in combined)
        ):
            return "import_error"
        if "filenotfounderror" in error_type.lower() or "no such file" in combined or "not found" in combined:
            return "file_not_found"
        if "syntaxerror" in error_type.lower() or "syntax error" in combined:
            return "syntax_error"
        if "permissionerror" in error_type.lower() or "permission denied" in combined:
            return "permission_denied"
        if "timeouterror" in error_type.lower() or "timeout" in combined or "timed out" in combined:
            return "timeout"
        if "connectionerror" in error_type.lower() or (
            "connection" in combined and ("refused" in combined or "error" in combined)
        ):
            return "connection_error"
        if "memoryerror" in error_type.lower() or "oom" in combined or "out of memory" in combined:
            return "memory_error"
        if "typeerror" in error_type.lower():
            return "type_error"
        if "assertionerror" in error_type.lower() or "assertionerror" in combined:
            return "assertion_error"
        if "build" in combined and ("fail" in combined or "error" in combined):
            return "build_error"
        if "test" in combined and ("fail" in combined or "error" in combined):
            return "test_failure"
        if "docker" in combined:
            return "docker_error"
        if "git" in combined and "error" in combined:
            return "git_error"
        if any(kw in combined for kw in ("traceback", "exception", "runtimeerror")):
            return "runtime_exception"
        return "general_error"

    # ------------------------------------------------------------------
    # Slug helpers
    # ------------------------------------------------------------------

    def _fix_slug(self, repair_command: str) -> str:
        """Short slug for a repair command, suitable for use in a lesson key."""
        return self._slugify(repair_command, 40)

    # ------------------------------------------------------------------
    # Compiled pattern factories (called once in __init__)
    # ------------------------------------------------------------------

    def _error_indicators(self) -> re.Pattern:
        return re.compile(
            r"(?:\b(?:error|exception|traceback|failed|failure|errno"
            r"|no\s+such\s+file|permission\s+denied|not\s+found"
            r"|syntax\s+error|segfault|crash|exit\s+code\s+[1-9])\b"
            r"|(?:ModuleNotFoundError|ImportError|FileNotFoundError|SyntaxError"
            r"|PermissionError|RuntimeError|TypeError|ValueError|AttributeError"
            r"|NameError|KeyError|IndexError|OSError|IOError))",
            re.IGNORECASE,
        )

    def _fix_indicators(self) -> re.Pattern:
        return re.compile(
            r"\b(?:fixed|resolved|repaired|corrected|patched|success"
            r"|done|ok\b|complete|installed|created|updated)\b",
            re.IGNORECASE,
        )

    # ------------------------------------------------------------------
    # Internal utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _result_text(call: dict) -> str:
        return str(call.get("result") or call.get("output") or "")

    @staticmethod
    def _first_arg(args: dict) -> str:
        for key in ("command", "cmd", "code", "input", "text", "content", "path", "file"):
            val = args.get(key)
            if val:
                return str(val)[:80]
        for val in args.values():
            if val and isinstance(val, (str, int, float)):
                return str(val)[:80]
        return ""
