"""KRYTH Reflection & Learning Engine.

Single import point:
    from agent.reflection import ReflectionEngine, get_reflection_engine
    from agent.reflection import ReflectionInput, ReflectionOutput
    from agent.reflection import KnowledgeLesson, ExperienceRecord
    from agent.reflection import PipelineConfig, ReflectionPipeline
    from agent.reflection import ReflectionAnalytics, get_reflection_analytics
"""

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionStatus,
    ReflectionInput, ReflectionOutput, ExperienceRecord,
)
from agent.reflection.extractor import (
    BaseReflector, KnowledgeExtractor, build_default_extractor,
)
from agent.reflection.scoring_engine import (
    ReflectionScoringEngine, ScoringWeights, ScoringContext,
)
from agent.reflection.noise_filter import NoiseFilter, FilterRule
from agent.reflection.pipeline import ReflectionPipeline, PipelineConfig
from agent.reflection.engine import ReflectionEngine, get_reflection_engine
from agent.reflection.analytics import ReflectionAnalytics, get_reflection_analytics

__all__ = [
    "ReflectionEngine", "get_reflection_engine",
    "ReflectionInput", "ReflectionOutput",
    "KnowledgeLesson", "LessonKind", "LessonScope", "ReflectionStatus",
    "ExperienceRecord",
    "BaseReflector", "KnowledgeExtractor", "build_default_extractor",
    "ReflectionScoringEngine", "ScoringWeights", "ScoringContext",
    "NoiseFilter", "FilterRule",
    "ReflectionPipeline", "PipelineConfig",
    "ReflectionAnalytics", "get_reflection_analytics",
]
