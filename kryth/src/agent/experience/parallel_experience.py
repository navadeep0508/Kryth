"""Parallel Strategy Experience — tracks which agent count / depth works best."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from agent.experience.store import ExperienceRecord, ExperienceStore


@dataclass
class ParallelExperience:
    task_type: str
    agent_count: int
    parallel_depth: int
    execution_turns: int
    conflict_count: int
    merge_problems: int
    success_rate: float
    composite_score: float
    reuse_count: int


class ParallelExperienceEngine:

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def record(
        self,
        task_type: str,
        agent_count: int,
        parallel_depth: int,
        execution_turns: int,
        conflict_count: int,
        merge_problems: int,
        success: bool,
    ) -> None:
        rec = ExperienceRecord(
            id=None,
            project_hash=self._store._ph,
            kind="parallel",
            title=f"{task_type}:agents={agent_count}:depth={parallel_depth}",
            summary=f"{task_type} with {agent_count} agents, depth {parallel_depth}",
            tags=[task_type, f"agents={agent_count}"],
            source_refs={},
            outcome="success" if success else "failure",
            importance=0.7,
            confidence=0.8 if success else 0.3,
            success_rate=1.0 if success else 0.0,
            reuse_count=0,
            novelty=0.4,
            recency_ts=time.time(),
            created_at=time.time(),
            composite_score=0.0,
            extra={
                "task_type": task_type,
                "agent_count": agent_count,
                "parallel_depth": parallel_depth,
                "execution_turns": execution_turns,
                "conflict_count": conflict_count,
                "merge_problems": merge_problems,
            },
        )
        self._store.insert(rec)

    def recommend_agent_count(self, task_type: str) -> Tuple[int, float]:
        """Return (recommended_agent_count, confidence) for a task type."""
        hits = self._store.search_fts(task_type, kind="parallel", limit=20)
        if not hits:
            return 1, 0.0

        # Group by agent count, compute average success rate
        groups: Dict[int, List[float]] = {}
        for rec in hits:
            ac = rec.extra.get("agent_count", 1)
            groups.setdefault(ac, []).append(rec.success_rate)

        best_ac = max(
            groups,
            key=lambda ac: sum(groups[ac]) / len(groups[ac]) * len(groups[ac]) ** 0.5,
        )
        best_sr = sum(groups[best_ac]) / len(groups[best_ac])
        confidence = min(1.0, best_sr * (len(groups[best_ac]) / 5) ** 0.5)
        return best_ac, round(confidence, 2)

    def get_all(self) -> List[ParallelExperience]:
        recs = self._store.get_by_kind("parallel", limit=100)
        return [
            ParallelExperience(
                task_type=r.extra.get("task_type", ""),
                agent_count=r.extra.get("agent_count", 1),
                parallel_depth=r.extra.get("parallel_depth", 1),
                execution_turns=r.extra.get("execution_turns", 0),
                conflict_count=r.extra.get("conflict_count", 0),
                merge_problems=r.extra.get("merge_problems", 0),
                success_rate=r.success_rate,
                composite_score=r.composite_score,
                reuse_count=r.reuse_count,
            )
            for r in recs
        ]
