from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from agent.reflection.experience_record import (
    KnowledgeLesson, LessonKind, LessonScope, ReflectionStatus,
    ReflectionInput, ReflectionOutput, ExperienceRecord,
)
from agent.reflection.pipeline import ReflectionPipeline, PipelineConfig
from agent.reflection.extractor import build_default_extractor


class ReflectionEngine:
    """
    Public API for the KRYTH Reflection & Learning Engine.

    Usage:
        engine = ReflectionEngine(project_hash="abc123")
        output = engine.reflect(inp)
        # or
        output = engine.reflect_from_dict({
            "task_description": "...",
            "tool_calls": [...],
            ...
        })
    """

    def __init__(
        self,
        project_hash: str = "",
        config: Optional[PipelineConfig] = None,
        ui_callback: Optional[Callable] = None,
    ) -> None:
        self.project_hash = project_hash
        if config is None:
            config = PipelineConfig(ui_callback=ui_callback)
        elif ui_callback is not None:
            config.ui_callback = ui_callback
        self._pipeline = ReflectionPipeline(
            project_hash=project_hash,
            config=config,
            extractor=build_default_extractor(project_hash),
        )

    # ---- Core API ----

    def reflect(self, inp: ReflectionInput) -> ReflectionOutput:
        """Run the full reflection pipeline on a completed task."""
        return self._pipeline.run(inp)

    def reflect_from_dict(self, data: Dict[str, Any]) -> ReflectionOutput:
        """
        Build a ReflectionInput from a plain dict and run reflection.

        Accepted keys (all optional except task_description):
            task_description, task_id, project_hash, agent_id,
            tool_calls, session_messages, final_output,
            terminal_events, repair_events, dag_tasks,
            team_events, decisions, file_changes,
            started_at, ended_at
        """
        inp = ReflectionInput(
            task_description=data.get("task_description", ""),
            task_id=data.get("task_id", ""),
            project_hash=data.get("project_hash", self.project_hash),
            agent_id=data.get("agent_id", ""),
            tool_calls=data.get("tool_calls", []),
            session_messages=data.get("session_messages", []),
            final_output=data.get("final_output", ""),
            terminal_events=data.get("terminal_events", []),
            repair_events=data.get("repair_events", []),
            dag_tasks=data.get("dag_tasks", []),
            team_events=data.get("team_events", []),
            decisions=data.get("decisions", []),
            file_changes=data.get("file_changes", []),
            started_at=data.get("started_at", time.time()),
            ended_at=data.get("ended_at"),
        )
        return self.reflect(inp)

    # ---- Convenience wrappers matching the spec API ----

    def analyze(self, inp: ReflectionInput) -> ReflectionOutput:
        """Alias for reflect() — matches the analyze() API from the spec."""
        return self.reflect(inp)

    def extract(self, inp: ReflectionInput) -> List[KnowledgeLesson]:
        """Extract lessons only, without scoring/filtering/storage."""
        extractor = build_default_extractor(self.project_hash)
        return extractor.extract_all(inp)

    def score(self, lessons: List[KnowledgeLesson]) -> List[KnowledgeLesson]:
        """Score a list of lessons and return them sorted."""
        from agent.reflection.scoring_engine import ReflectionScoringEngine
        scorer = ReflectionScoringEngine()
        return scorer.score_all(lessons)

    def summarize(self, inp: ReflectionInput) -> str:
        """Return a one-sentence task summary without running the full pipeline."""
        return self._pipeline._summarize(inp)

    def promote(self, lessons: List[KnowledgeLesson], project_hash: str = "") -> List[str]:
        """
        Attempt to promote a list of already-scored lessons directly to MemoryManager.
        Returns list of keys that were stored.
        """
        ph = project_hash or self.project_hash
        stored: List[str] = []
        try:
            self._pipeline._store_in_memory(lessons, ph)
            stored = [
                l.key for l in lessons
                if l.status.value in ("stored", "approved")
            ]
        except Exception:
            pass
        return stored

    # ---- Experience queries (for future Experience Engine) ----

    def query_experiences(
        self,
        outcome: Optional[str] = None,
        task_category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Query past ExperienceRecords."""
        return self._pipeline.query_experiences(
            outcome=outcome,
            task_category=task_category,
            limit=limit,
        )

    # ---- Status ----

    def get_pipeline_info(self) -> Dict[str, Any]:
        return {
            "project_hash": self.project_hash,
            "registered_reflectors": self._pipeline.extractor.registered_reflector_names(),
            "config": {
                "promotion_threshold": self._pipeline.config.promotion_threshold,
                "approval_threshold": self._pipeline.config.require_approval_above,
                "noise_min_score": self._pipeline.config.min_noise_score,
                "use_memory_manager": self._pipeline.config.use_memory_manager,
                "use_knowledge_graph": self._pipeline.config.use_knowledge_graph,
                "persist_experience": self._pipeline.config.persist_experience,
            },
        }


# ---- Module-level singleton ----

_engines: Dict[str, ReflectionEngine] = {}


def get_reflection_engine(
    project_hash: str = "",
    ui_callback: Optional[Callable] = None,
    config: Optional[PipelineConfig] = None,
) -> ReflectionEngine:
    if project_hash not in _engines:
        _engines[project_hash] = ReflectionEngine(
            project_hash=project_hash,
            config=config,
            ui_callback=ui_callback,
        )
    return _engines[project_hash]
