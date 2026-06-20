"""Executive Memory — records executive decisions and outcomes for learning."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.executive.executive_state import ExecutiveDecision, RiskLevel

logger = logging.getLogger(__name__)
_FILE = ".kryth/executive_memory.jsonl"


@dataclass
class ExecutiveRecord:
    session_id: str
    mission: str
    decision: str          # ExecutiveDecision value
    rationale: str
    confidence_overall: float
    risk_level: str
    success: bool
    escalated: bool
    replan_count: int
    quality_overall: float
    token_pct: float
    elapsed_s: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ExecutiveMemory:
    """Persist and query executive decision outcomes."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._file   = Path(project_root).resolve() / _FILE
        self._lock   = threading.Lock()
        self._cache: list[ExecutiveRecord] = []
        self._loaded = False

    def record(self, rec: ExecutiveRecord) -> None:
        with self._lock:
            self._cache.append(rec)
        self._append(rec)

    def intervention_success_rate(self, decision: ExecutiveDecision) -> float:
        """How often did a given intervention lead to mission success?"""
        self._ensure_loaded()
        with self._lock:
            matching = [
                r for r in self._cache
                if r.decision == decision.value
            ]
        if not matching:
            return -1.0
        return sum(r.success for r in matching) / len(matching)

    def stats(self) -> dict[str, Any]:
        self._ensure_loaded()
        with self._lock:
            records = list(self._cache)
        if not records:
            return {"total": 0}
        total   = len(records)
        success = sum(r.success for r in records)
        return {
            "total":               total,
            "success_count":       success,
            "success_rate":        success / total,
            "escalation_rate":     sum(r.escalated for r in records) / total,
            "avg_replan_count":    sum(r.replan_count for r in records) / total,
            "avg_confidence":      sum(r.confidence_overall for r in records) / total,
            "avg_quality":         sum(r.quality_overall for r in records) / total,
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _append(self, rec: ExecutiveRecord) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "session_id":         rec.session_id,
            "mission":            rec.mission,
            "decision":           rec.decision,
            "rationale":          rec.rationale,
            "confidence_overall": rec.confidence_overall,
            "risk_level":         rec.risk_level,
            "success":            rec.success,
            "escalated":          rec.escalated,
            "replan_count":       rec.replan_count,
            "quality_overall":    rec.quality_overall,
            "token_pct":          rec.token_pct,
            "elapsed_s":          rec.elapsed_s,
            "timestamp":          rec.timestamp,
        }) + "\n"
        with self._lock:
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(line)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                for line in self._file.read_text(encoding="utf-8").splitlines():
                    try:
                        self._cache.append(ExecutiveRecord(**json.loads(line)))
                    except Exception:
                        pass
            except FileNotFoundError:
                pass
            self._loaded = True
