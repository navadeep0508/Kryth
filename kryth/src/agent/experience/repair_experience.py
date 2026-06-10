"""Repair Experience Engine — instantly reuses known fixes.

Before reasoning, searches FailureMemory and ExperienceStore for exact/
similar error → repair matches. If confidence is high enough, returns the
cached repair without any LLM call.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore


@dataclass
class RepairExperience:
    error_type: str
    error_pattern: str
    repair_commands: List[str]
    confidence: float          # 0.0 – 1.0
    success_rate: float
    source: str                # "failure_memory" | "execution_memory" | "experience_store"


CONFIDENCE_THRESHOLD = 0.75   # above this: skip LLM, use cached repair directly


class RepairExperienceEngine:
    """Looks up historical repairs before spending an LLM call."""

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def lookup(self, error_type: str, error_message: str) -> Optional[RepairExperience]:
        """Return a high-confidence repair if one is known."""
        # 1. FailureMemory (most authoritative)
        try:
            from agent.memory.failure_memory import FailureMemory
            fm = FailureMemory()
            repairs = fm.get_repair_strategy(error_type, error_message)
            if repairs:
                return RepairExperience(
                    error_type=error_type,
                    error_pattern=error_message[:200],
                    repair_commands=repairs,
                    confidence=0.90,
                    success_rate=1.0,
                    source="failure_memory",
                )
        except Exception:
            pass

        # 2. ExecutionMemory repair_patterns
        try:
            from agent.memory.execution_memory import ExecutionMemory
            em = ExecutionMemory()
            repairs = em.get_repair_strategy(error_type, error_message)
            if repairs:
                return RepairExperience(
                    error_type=error_type,
                    error_pattern=error_message[:200],
                    repair_commands=repairs,
                    confidence=0.85,
                    success_rate=1.0,
                    source="execution_memory",
                )
        except Exception:
            pass

        # 3. Experience store FTS
        query = f"{error_type} {error_message[:100]}"
        hits = self._store.search_fts(query, kind="repair", limit=3)
        if hits:
            best = hits[0]
            if best.success_rate >= 0.7:
                return RepairExperience(
                    error_type=error_type,
                    error_pattern=error_message[:200],
                    repair_commands=best.extra.get("repair_commands", []),
                    confidence=best.composite_score,
                    success_rate=best.success_rate,
                    source="experience_store",
                )
        return None

    def record(
        self,
        error_type: str,
        error_message: str,
        repair_commands: List[str],
        success: bool,
    ) -> None:
        """Record the outcome of a repair attempt."""
        # Write to FailureMemory (source of truth)
        try:
            from agent.memory.failure_memory import FailureMemory
            fm = FailureMemory()
            fid = fm.record_failure(
                failure_type="runtime",
                error_type=error_type,
                error_message=error_message,
                context={},
            )
            for cmd in repair_commands:
                fm.record_repair_attempt(fid, cmd, "success" if success else "failure")
            if success:
                fm.mark_resolved(fid, " | ".join(repair_commands))
        except Exception:
            pass

        # Also index in experience store for FTS retrieval
        rec = ExperienceRecord(
            id=None,
            project_hash=self._store._ph,
            kind="repair",
            title=f"{error_type}: {error_message[:60]}",
            summary=f"Fix: {', '.join(repair_commands[:3])}",
            tags=[error_type],
            source_refs={},
            outcome="success" if success else "failure",
            importance=0.85,
            confidence=0.9 if success else 0.2,
            success_rate=1.0 if success else 0.0,
            reuse_count=0,
            novelty=0.3,
            recency_ts=time.time(),
            created_at=time.time(),
            composite_score=0.0,
            extra={"error_type": error_type, "repair_commands": repair_commands},
        )
        self._store.insert(rec)
