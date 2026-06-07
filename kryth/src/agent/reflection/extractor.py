from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Type

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionInput
)


class BaseReflector(ABC):
    """Base class for all domain-specific reflectors."""

    name: str = "base"

    def __init__(self, project_hash: str = "") -> None:
        self.project_hash = project_hash

    @abstractmethod
    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        """Extract knowledge lessons from the reflection input."""

    def _make_lesson(
        self,
        key: str,
        kind: LessonKind,
        scope: LessonScope,
        value: Any,
        importance: float = 0.5,
        confidence: float = 0.5,
        novelty: float = 0.5,
        reuse_potential: float = 0.6,
        success_rate: float = 0.7,
        metadata: Dict[str, Any] = None,
        ttl: Optional[float] = None,
    ) -> KnowledgeLesson:
        return KnowledgeLesson(
            key=key,
            kind=kind,
            scope=scope,
            value=value,
            importance=importance,
            confidence=confidence,
            novelty=novelty,
            reuse_potential=reuse_potential,
            success_rate=success_rate,
            source=self.name,
            project_hash=self.project_hash,
            ttl=ttl,
            metadata=metadata or {},
        )

    def _slugify(self, text: str, max_len: int = 48) -> str:
        import re
        text = re.sub(r"[^\w\s\-]", "", text.lower())
        text = re.sub(r"[\s\-]+", "_", text).strip("_")
        return text[:max_len]


class KnowledgeExtractor:
    """
    Runs all registered reflectors against a ReflectionInput
    and merges their lessons, deduplicating by key (last-writer-wins).
    """

    def __init__(self, project_hash: str = "") -> None:
        self.project_hash = project_hash
        self._reflectors: List[BaseReflector] = []

    def register(self, reflector: BaseReflector) -> None:
        self._reflectors.append(reflector)

    def extract_all(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        """Run all reflectors; merge and deduplicate lessons by key."""
        all_lessons: Dict[str, KnowledgeLesson] = {}

        for reflector in self._reflectors:
            try:
                lessons = reflector.extract(inp)
                for lesson in lessons:
                    lesson.project_hash = lesson.project_hash or self.project_hash
                    all_lessons[lesson.key] = lesson
            except Exception:
                # One broken reflector must not stop the pipeline
                pass

        return list(all_lessons.values())

    def registered_reflector_names(self) -> List[str]:
        return [r.name for r in self._reflectors]


def build_default_extractor(project_hash: str = "") -> KnowledgeExtractor:
    """
    Build a KnowledgeExtractor pre-loaded with all standard reflectors.
    Imports are deferred so the function works even if optional reflectors
    are missing.
    """
    extractor = KnowledgeExtractor(project_hash=project_hash)

    reflector_modules = [
        ("agent.reflection.terminal_reflector", "TerminalReflector"),
        ("agent.reflection.repair_reflector",   "RepairReflector"),
        ("agent.reflection.workflow_reflector", "WorkflowReflector"),
        ("agent.reflection.team_reflector",     "TeamReflector"),
        ("agent.reflection.decision_reflector", "DecisionReflector"),
        ("agent.reflection.build_reflector",    "BuildReflector"),
    ]

    for module_path, class_name in reflector_modules:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            extractor.register(cls(project_hash=project_hash))
        except (ImportError, AttributeError):
            pass

    return extractor
