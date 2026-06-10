from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent.reflection.experience_record import KnowledgeLesson, ReflectionStatus

# Trivial read-only commands that produce no reusable knowledge
_TRIVIAL_COMMAND_SUFFIXES = frozenset({"ls", "pwd", "echo", "date", "whoami", "id"})

# ANSI escape code pattern
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Consecutive newline pattern (>5 in a row)
_MULTI_NEWLINE_RE = re.compile(r"\n{6,}")


@dataclass
class FilterRule:
    name: str
    description: str


class NoiseFilter:
    """
    Removes lessons that are not worth storing.

    Forbidden patterns (from spec):
    - Raw terminal output (large text blobs)
    - Duplicate keys already in seen_keys
    - Low composite_score below threshold
    - Repeated repairs that have already been stored
    - Lessons with low confidence AND low importance (both < 0.35)
    - Very short or trivially obvious values
    """

    DEFAULT_MIN_SCORE = 0.30
    MAX_VALUE_SIZE = 2000

    def __init__(
        self,
        min_score: float = DEFAULT_MIN_SCORE,
        seen_keys: Optional[set] = None,
        max_value_size: int = MAX_VALUE_SIZE,
    ) -> None:
        self.min_score = min_score
        self.seen_keys: set = seen_keys or set()
        self.max_value_size = max_value_size
        self._rules: List[FilterRule] = self._build_rules()
        self._stats: Dict[str, int] = {r.name: 0 for r in self._rules}
        self._stats["total_seen"] = 0
        self._stats["total_kept"] = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def filter(
        self, lessons: List[KnowledgeLesson]
    ) -> Tuple[List[KnowledgeLesson], List[str]]:
        kept: List[KnowledgeLesson] = []
        filtered_keys: List[str] = []

        for lesson in lessons:
            self._stats["total_seen"] += 1
            noise, reason = self.is_noise(lesson)
            if noise:
                lesson.status = ReflectionStatus.FILTERED
                self._stats[reason] = self._stats.get(reason, 0) + 1
                filtered_keys.append(lesson.key)
            else:
                self._stats["total_kept"] += 1
                kept.append(lesson)

        return kept, filtered_keys

    def is_noise(self, lesson: KnowledgeLesson) -> Tuple[bool, str]:
        # 1. Score gate
        if lesson.composite_score < self.min_score:
            return True, "low_score"

        # 2. Duplicate key
        if lesson.key in self.seen_keys:
            return True, "duplicate"

        value_str = str(lesson.value) if lesson.value is not None else ""

        # 3. Value too large
        if len(value_str) > self.max_value_size:
            return True, "too_large"

        # 4. Low quality: both dimensions weak
        if lesson.importance < 0.35 and lesson.confidence < 0.35:
            return True, "low_quality"

        # 5. Empty value
        if self._is_empty(lesson.value):
            return True, "empty_value"

        # 6. Trivial command
        if self._is_trivial_command(lesson.key):
            return True, "trivial_command"

        # 7. Raw terminal output (ANSI codes or walls of newlines)
        if _ANSI_RE.search(value_str) or _MULTI_NEWLINE_RE.search(value_str):
            return True, "raw_output"

        return False, ""

    def add_seen_key(self, key: str) -> None:
        self.seen_keys.add(key)

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        self._stats = {r.name: 0 for r in self._rules}
        self._stats["total_seen"] = 0
        self._stats["total_kept"] = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_rules(self) -> List[FilterRule]:
        return [
            FilterRule("low_score",       "composite_score below minimum threshold"),
            FilterRule("duplicate",       "key already stored in memory"),
            FilterRule("too_large",       "lesson value exceeds max size"),
            FilterRule("low_quality",     "both importance and confidence are too low"),
            FilterRule("empty_value",     "lesson has no usable value"),
            FilterRule("trivial_command", "command is a no-value read-only operation"),
            FilterRule("raw_output",      "value appears to be raw terminal output"),
        ]

    @staticmethod
    def _is_empty(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return value.strip() == ""
        if isinstance(value, (dict, list)):
            return len(value) == 0
        return False

    @staticmethod
    def _is_trivial_command(key: str) -> bool:
        # Keys like "command:success:ls" or "command:success:pwd"
        parts = key.split(":")
        if len(parts) >= 1 and parts[0] == "command":
            last = parts[-1].strip().lower()
            if last in _TRIVIAL_COMMAND_SUFFIXES:
                return True
        return False
