"""Decision Experience Engine — remembers why decisions succeeded.

Wraps DecisionMemory (primary) and indexes decisions in ExperienceStore
for cross-task similarity search.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore


@dataclass
class DecisionExperience:
    title: str
    choice: str
    rationale: str
    alternatives: List[str]
    outcome: str            # "successful" | "mixed" | "failed"
    confidence: float
    reuse_count: int


class DecisionExperienceEngine:

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def record(
        self,
        title: str,
        choice: str,
        rationale: str,
        alternatives: List[str],
        outcome: str = "successful",
    ) -> None:
        """Record a decision and its outcome."""
        try:
            from agent.memory.decision_memory import DecisionMemory
            dm = DecisionMemory()
            dm.record(
                title=title,
                choice=choice,
                rationale=rationale,
                alternatives=alternatives,
                tradeoffs={"pro": [], "con": []},
                decision_type="other",
                author="experience_engine",
            )
        except Exception:
            pass

        success = outcome == "successful"
        rec = ExperienceRecord(
            id=None,
            project_hash=self._store._ph,
            kind="decision",
            title=title,
            summary=f"Chose {choice}: {rationale[:120]}",
            tags=[choice] + alternatives[:2],
            source_refs={},
            outcome=outcome,
            importance=0.75,
            confidence=0.85 if success else 0.4,
            success_rate=1.0 if success else 0.3,
            reuse_count=0,
            novelty=0.5,
            recency_ts=time.time(),
            created_at=time.time(),
            composite_score=0.0,
            extra={"choice": choice, "rationale": rationale, "alternatives": alternatives},
        )
        self._store.insert(rec)

    def lookup(self, topic: str) -> Optional[DecisionExperience]:
        """Return the best known decision for a topic."""
        # Try DecisionMemory first
        try:
            from agent.memory.decision_memory import DecisionMemory
            dm = DecisionMemory()
            dec = dm.get(topic)
            if dec:
                return DecisionExperience(
                    title=dec.title,
                    choice=dec.choice,
                    rationale=dec.rationale,
                    alternatives=dec.alternatives,
                    outcome="successful",
                    confidence=0.9,
                    reuse_count=0,
                )
        except Exception:
            pass

        hits = self._store.search_fts(topic, kind="decision", limit=1)
        if hits:
            rec = hits[0]
            return DecisionExperience(
                title=rec.title,
                choice=rec.extra.get("choice", ""),
                rationale=rec.extra.get("rationale", ""),
                alternatives=rec.extra.get("alternatives", []),
                outcome=rec.outcome,
                confidence=rec.confidence,
                reuse_count=rec.reuse_count,
            )
        return None
