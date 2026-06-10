"""Similar Task Engine — finds historical tasks that resemble a new request.

Queries across:
  ExperienceStore (FTS + composite score)
  ExecutionMemory (successful command history)
  WorkflowMemory  (known workflow patterns)

Never stores anything new — only reads existing stores.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore


@dataclass
class SimilarTask:
    title: str
    summary: str
    kind: str
    similarity_score: float      # 0.0 – 1.0
    historical_success_rate: float
    best_workflow_steps: List[str]
    best_team_roles: List[str]
    known_repairs: List[str]     # repair commands that worked
    known_risks: List[str]
    source_record_id: Optional[int] = None
    reuse_count: int = 0


@dataclass
class SimilarTaskSearchResult:
    query: str
    matches: List[SimilarTask]
    confidence: float            # 0.0 – 1.0 overall confidence in results
    searched_systems: List[str]
    elapsed_ms: float
    best_match: Optional[SimilarTask] = None

    def has_high_confidence(self, threshold: float = 0.70) -> bool:
        return bool(self.best_match and self.best_match.similarity_score >= threshold)


class SimilarTaskEngine:
    """Searches across all memory systems for similar past tasks."""

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def search(
        self,
        user_input: str,
        top_k: int = 5,
        min_similarity: float = 0.30,
    ) -> SimilarTaskSearchResult:
        t0 = time.time()
        searched: List[str] = []
        candidates: List[SimilarTask] = []

        # 1. Experience store FTS
        exp_hits = self._store.search_fts(user_input, limit=top_k * 2)
        searched.append("experience_store")
        for rec in exp_hits:
            sim = self._score_similarity(user_input, rec)
            if sim >= min_similarity:
                candidates.append(
                    SimilarTask(
                        title=rec.title,
                        summary=rec.summary,
                        kind=rec.kind,
                        similarity_score=sim,
                        historical_success_rate=rec.success_rate,
                        best_workflow_steps=rec.extra.get("workflow_steps", []),
                        best_team_roles=rec.extra.get("team_roles", []),
                        known_repairs=rec.extra.get("repairs", []),
                        known_risks=rec.extra.get("risks", []),
                        source_record_id=rec.id,
                        reuse_count=rec.reuse_count,
                    )
                )

        # 2. WorkflowMemory
        try:
            from agent.memory.workflow_memory import WorkflowMemory
            wm = WorkflowMemory()
            workflows = wm.list_workflows()
            searched.append("workflow_memory")
            for wf in workflows[:top_k]:
                sim = self._text_similarity(user_input, wf.name + " " + " ".join(wf.steps))
                if sim >= min_similarity:
                    candidates.append(
                        SimilarTask(
                            title=wf.name,
                            summary=f"Workflow: {', '.join(wf.steps[:5])}",
                            kind="workflow",
                            similarity_score=sim * 0.8,  # lower weight
                            historical_success_rate=wf.success_rate,
                            best_workflow_steps=wf.steps,
                            best_team_roles=[],
                            known_repairs=[],
                            known_risks=[],
                        )
                    )
        except Exception:
            pass

        # 3. ExecutionMemory — successful commands
        try:
            from agent.memory.execution_memory import ExecutionMemory
            em = ExecutionMemory()
            cmds = em.get_successful_commands("build", limit=20)
            searched.append("execution_memory")
            for cmd_rec in cmds:
                sim = self._text_similarity(user_input, cmd_rec.command)
                if sim >= min_similarity:
                    candidates.append(
                        SimilarTask(
                            title=cmd_rec.command[:80],
                            summary=f"Successful {cmd_rec.command_type} command",
                            kind="terminal",
                            similarity_score=sim * 0.6,
                            historical_success_rate=1.0,
                            best_workflow_steps=[cmd_rec.command],
                            best_team_roles=[],
                            known_repairs=[],
                            known_risks=[],
                        )
                    )
        except Exception:
            pass

        # Sort by similarity descending
        candidates.sort(key=lambda c: -c.similarity_score)
        top = candidates[:top_k]

        confidence = top[0].similarity_score if top else 0.0
        elapsed = (time.time() - t0) * 1000

        return SimilarTaskSearchResult(
            query=user_input,
            matches=top,
            confidence=confidence,
            searched_systems=searched,
            elapsed_ms=round(elapsed, 1),
            best_match=top[0] if top else None,
        )

    def _score_similarity(self, query: str, rec: ExperienceRecord) -> float:
        """Combine text similarity with the experience composite score."""
        text_sim = self._text_similarity(
            query, f"{rec.title} {rec.summary} {' '.join(rec.tags)}"
        )
        return 0.6 * text_sim + 0.4 * rec.composite_score

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """Fast token Jaccard similarity — no embeddings needed."""
        a_words = set(a.lower().split())
        b_words = set(b.lower().split())
        if not a_words or not b_words:
            return 0.0
        intersection = a_words & b_words
        union = a_words | b_words
        return len(intersection) / len(union)
