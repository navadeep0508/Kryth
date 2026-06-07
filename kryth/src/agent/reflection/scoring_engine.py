from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionStatus,
)


@dataclass
class ScoringWeights:
    importance: float = 0.30
    confidence: float = 0.20
    novelty: float = 0.20
    reuse_potential: float = 0.15
    success_rate: float = 0.15

    def validate(self) -> None:
        total = (
            self.importance + self.confidence + self.novelty
            + self.reuse_potential + self.success_rate
        )
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total}")


@dataclass
class ScoringContext:
    existing_keys: set = field(default_factory=set)
    recently_accessed_keys: set = field(default_factory=set)
    project_has_tests: bool = True
    project_has_ci: bool = False
    task_category: str = ""


class ReflectionScoringEngine:
    def __init__(self, weights: Optional[ScoringWeights] = None) -> None:
        self.weights = weights or ScoringWeights()
        self.weights.validate()

    def score(self, lesson: KnowledgeLesson, ctx: Optional[ScoringContext] = None) -> KnowledgeLesson:
        w = self.weights

        novelty = self._compute_novelty(lesson, ctx)
        reuse = self._compute_reuse_potential(lesson, ctx)

        # Update lesson fields if ctx was provided
        if ctx is not None:
            lesson.novelty = novelty
            lesson.reuse_potential = reuse

        raw = (
            w.importance * lesson.importance
            + w.confidence * lesson.confidence
            + w.novelty * lesson.novelty
            + w.reuse_potential * lesson.reuse_potential
            + w.success_rate * lesson.success_rate
        )

        # Kind-based bonuses
        if lesson.kind == LessonKind.REPAIR:
            raw += 0.05
        elif lesson.kind == LessonKind.DECISION:
            raw += 0.05
        elif lesson.kind == LessonKind.USER_FACT:
            raw += 0.10
        elif lesson.kind == LessonKind.WORKFLOW and lesson.success_rate > 0.8:
            raw += 0.05

        # Scope bonus
        if lesson.scope == LessonScope.LONG_TERM:
            raw += 0.03

        lesson.composite_score = min(raw, 1.0)
        lesson.status = ReflectionStatus.SCORED
        return lesson

    def score_all(
        self,
        lessons: List[KnowledgeLesson],
        ctx: Optional[ScoringContext] = None,
    ) -> List[KnowledgeLesson]:
        for lesson in lessons:
            self.score(lesson, ctx)
        return sorted(lessons, key=lambda l: l.composite_score, reverse=True)

    def _compute_novelty(self, lesson: KnowledgeLesson, ctx: Optional[ScoringContext]) -> float:
        if ctx is None:
            return lesson.novelty
        if lesson.key not in ctx.existing_keys:
            return 1.0
        # Key exists — check if this is likely a variant via prefix heuristic.
        # A "variant" is a key that shares a prefix with an existing key but
        # isn't an exact match (impossible here since we already know it IS in
        # existing_keys), so we use 0.1 for exact duplicates.
        # The 0.5 branch covers keys that *look* like prefixes of existing ones
        # — approximated by checking whether any existing key starts with the
        # lesson key (i.e. lesson key is a "parent" prefix).
        key = lesson.key
        for existing in ctx.existing_keys:
            if existing != key and existing.startswith(key):
                return 0.5
        return 0.1

    def _compute_reuse_potential(self, lesson: KnowledgeLesson, ctx: Optional[ScoringContext]) -> float:
        if ctx is None:
            return lesson.reuse_potential

        kind = lesson.kind
        if kind == LessonKind.REPAIR:
            return 0.9
        if kind == LessonKind.COMMAND:
            return 0.8 if lesson.key in ctx.recently_accessed_keys else 0.7
        if kind == LessonKind.WORKFLOW:
            return 0.85
        if kind == LessonKind.DECISION:
            return 0.75
        if kind == LessonKind.BUILD:
            return 0.8
        if kind == LessonKind.TEST:
            return 0.75
        if kind == LessonKind.USER_FACT:
            return 0.7
        if kind == LessonKind.TEAM:
            return 0.6
        if kind == LessonKind.FILE_CHANGE:
            return 0.4
        if kind == LessonKind.COMPLEXITY:
            return 0.3
        return 0.5

    def get_promotion_threshold(self, scope: LessonScope) -> float:
        thresholds = {
            LessonScope.WORKING: 0.40,
            LessonScope.SESSION: 0.50,
            LessonScope.PROJECT: 0.62,
            LessonScope.EXECUTION: 0.60,
            LessonScope.WORKFLOW: 0.65,
            LessonScope.LONG_TERM: 0.72,
        }
        return thresholds.get(scope, 0.55)
