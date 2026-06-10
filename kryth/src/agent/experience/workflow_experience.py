"""Workflow Experience Engine — learns successful workflows.

Wraps WorkflowMemory (reads/writes there) and cross-references with
ExperienceStore for scoring and similarity search.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore


@dataclass
class WorkflowExperience:
    name: str
    steps: List[str]
    intent_type: str
    success_rate: float
    avg_duration_sec: float
    failure_count: int
    composite_score: float
    reuse_count: int


class WorkflowExperienceEngine:
    """Learns and retrieves best workflow patterns."""

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def record(
        self,
        name: str,
        steps: List[str],
        intent_type: str,
        success: bool,
        duration_sec: float = 0.0,
    ) -> None:
        """Record a workflow execution outcome."""
        # Write to WorkflowMemory first (source of truth)
        try:
            from agent.memory.workflow_memory import WorkflowMemory
            wm = WorkflowMemory()
            wf = wm.get_best_workflow(intent_type)
            if wf is None:
                wf_id = wm.register_workflow(name, steps, intent_type)
            else:
                wf_id = wf.id
            wm.record_run(
                wf_id,
                outcome="success" if success else "failure",
                steps_completed=steps,
                failure_step=None,
                duration_sec=duration_sec,
            )
        except Exception:
            pass

        # Also store a scored experience record
        rec = ExperienceRecord(
            id=None,
            project_hash=self._store._ph,
            kind="workflow",
            title=name,
            summary=f"{intent_type}: {', '.join(steps[:5])}",
            tags=[intent_type] + steps[:3],
            source_refs={},
            outcome="success" if success else "failure",
            importance=0.65,
            confidence=0.8 if success else 0.3,
            success_rate=1.0 if success else 0.0,
            reuse_count=0,
            novelty=0.4,
            recency_ts=time.time(),
            created_at=time.time(),
            composite_score=0.0,
            extra={"steps": steps, "intent_type": intent_type, "duration_sec": duration_sec},
        )
        self._store.insert(rec)

    def recommend(self, intent_type: str) -> Optional[WorkflowExperience]:
        """Return the best known workflow for a given intent type."""
        # First try WorkflowMemory (authoritative)
        try:
            from agent.memory.workflow_memory import WorkflowMemory
            wm = WorkflowMemory()
            wf = wm.get_best_workflow(intent_type, min_success_rate=0.6)
            if wf:
                return WorkflowExperience(
                    name=wf.name,
                    steps=wf.steps,
                    intent_type=intent_type,
                    success_rate=wf.success_rate,
                    avg_duration_sec=wf.avg_duration_sec or 0.0,
                    failure_count=wf.fail_count,
                    composite_score=0.0,
                    reuse_count=wf.success_count + wf.fail_count,
                )
        except Exception:
            pass

        # Fall back to experience store
        hits = self._store.search_fts(intent_type, kind="workflow", limit=5)
        if hits:
            best = hits[0]
            return WorkflowExperience(
                name=best.title,
                steps=best.extra.get("steps", []),
                intent_type=best.extra.get("intent_type", intent_type),
                success_rate=best.success_rate,
                avg_duration_sec=best.extra.get("duration_sec", 0.0),
                failure_count=0,
                composite_score=best.composite_score,
                reuse_count=best.reuse_count,
            )
        return None
