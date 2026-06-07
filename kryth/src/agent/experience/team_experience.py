"""Team Experience Engine — learns which agent team structures work best.

Reads from ExperienceStore (kind='team'). Never stores to TeamMemory directly —
only creates experience records that point to TeamMemory outputs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore, get_store


@dataclass
class TeamExperience:
    task_type: str
    roles: List[str]           # e.g. ["backend specialist", "auth specialist"]
    strategy: str              # "single", "sequential", "parallel"
    agent_count: int
    execution_turns: int
    repair_count: int
    merge_conflicts: int
    success_rate: float
    composite_score: float
    reuse_count: int
    sample_tasks: List[str]    # example task descriptions that used this team


@dataclass
class TeamRecommendation:
    roles: List[str]
    strategy: str
    confidence: float
    evidence_count: int        # how many historical uses
    avg_success_rate: float
    reasoning: str


class TeamExperienceEngine:
    """Learns and recommends team structures from historical runs."""

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def record(
        self,
        task_description: str,
        roles: List[str],
        strategy: str,
        execution_turns: int,
        repair_count: int,
        merge_conflicts: int,
        success: bool,
    ) -> int:
        """Record the outcome of a team execution."""
        success_rate = 1.0 if success else 0.0
        extra = {
            "roles": roles,
            "strategy": strategy,
            "agent_count": len(roles),
            "execution_turns": execution_turns,
            "repair_count": repair_count,
            "merge_conflicts": merge_conflicts,
        }
        rec = ExperienceRecord(
            id=None,
            project_hash=self._store._ph,
            kind="team",
            title=f"{strategy}:{','.join(sorted(roles))}",
            summary=task_description[:200],
            tags=roles + [strategy],
            source_refs={},
            outcome="success" if success else "failure",
            importance=0.7,
            confidence=0.8 if success else 0.4,
            success_rate=success_rate,
            reuse_count=0,
            novelty=0.5,
            recency_ts=time.time(),
            created_at=time.time(),
            composite_score=0.0,
            extra=extra,
        )
        return self._store.insert(rec)

    def recommend(self, task_description: str, top_k: int = 3) -> Optional[TeamRecommendation]:
        """Recommend the best team structure for a task based on history."""
        hits = self._store.search_fts(task_description, kind="team", limit=top_k * 3)
        if not hits:
            hits = self._store.get_by_kind("team", limit=20, min_score=0.3)

        if not hits:
            return None

        # Group by (roles_set, strategy)
        groups: Dict[str, List[ExperienceRecord]] = {}
        for rec in hits:
            roles = sorted(rec.extra.get("roles", []))
            strategy = rec.extra.get("strategy", "sequential")
            key = f"{strategy}:{'|'.join(roles)}"
            groups.setdefault(key, []).append(rec)

        # Score each group
        best_key = max(
            groups,
            key=lambda k: (
                sum(r.success_rate for r in groups[k]) / len(groups[k])
                * len(groups[k]) ** 0.5  # weight by evidence count
            ),
        )
        best_recs = groups[best_key]
        avg_sr = sum(r.success_rate for r in best_recs) / len(best_recs)
        best_rec = max(best_recs, key=lambda r: r.composite_score)
        roles = best_rec.extra.get("roles", [])
        strategy = best_rec.extra.get("strategy", "sequential")

        confidence = min(1.0, avg_sr * (len(best_recs) / 5) ** 0.5)

        return TeamRecommendation(
            roles=roles,
            strategy=strategy,
            confidence=round(confidence, 2),
            evidence_count=len(best_recs),
            avg_success_rate=round(avg_sr, 2),
            reasoning=(
                f"Based on {len(best_recs)} similar task(s) with "
                f"{avg_sr:.0%} average success using {strategy} strategy"
            ),
        )

    def get_all(self, min_score: float = 0.3) -> List[TeamExperience]:
        recs = self._store.get_by_kind("team", limit=100, min_score=min_score)
        out: List[TeamExperience] = []
        for rec in recs:
            out.append(
                TeamExperience(
                    task_type=rec.summary[:60],
                    roles=rec.extra.get("roles", []),
                    strategy=rec.extra.get("strategy", "sequential"),
                    agent_count=rec.extra.get("agent_count", 1),
                    execution_turns=rec.extra.get("execution_turns", 0),
                    repair_count=rec.extra.get("repair_count", 0),
                    merge_conflicts=rec.extra.get("merge_conflicts", 0),
                    success_rate=rec.success_rate,
                    composite_score=rec.composite_score,
                    reuse_count=rec.reuse_count,
                    sample_tasks=[rec.summary],
                )
            )
        return out
