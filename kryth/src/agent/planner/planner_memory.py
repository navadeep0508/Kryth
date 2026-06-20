"""Planner Memory — records planning outcomes for continuous improvement.

Schema (JSONL at .kryth/planner_memory.jsonl):
  session_id, objective_goal, complexity, action_count, layer_count,
  worker_count, success, duration_s, replan_count, executors_used, timestamp
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)
_FILE = ".kryth/planner_memory.jsonl"


@dataclass
class PlanRecord:
    session_id: str
    objective_goal: str
    complexity: str
    action_count: int
    layer_count: int
    worker_count: int
    success: bool
    duration_s: float
    replan_count: int
    executors_used: list[str]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class PlannerMemory:
    """Persist planning outcomes and expose aggregate quality stats."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._file = Path(project_root).resolve() / _FILE
        self._lock = threading.Lock()
        self._cache: list[PlanRecord] = []
        self._loaded = False

    def record(self, rec: PlanRecord) -> None:
        with self._lock:
            self._cache.append(rec)
        self._append(rec)

    def success_rate(self, complexity: str | None = None) -> float:
        records = self._all()
        if complexity:
            records = [r for r in records if r.complexity == complexity]
        if not records:
            return -1.0
        return sum(r.success for r in records) / len(records)

    def avg_replan_count(self) -> float:
        records = self._all()
        if not records:
            return 0.0
        return sum(r.replan_count for r in records) / len(records)

    def executor_usage(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for r in self._all():
            for ex in r.executors_used:
                counts[ex] += 1
        return dict(counts)

    def stats(self) -> dict:
        records = self._all()
        if not records:
            return {"total": 0}
        success_count = sum(r.success for r in records)
        return {
            "total":              len(records),
            "success_count":      success_count,
            "success_rate":       success_count / len(records),
            "avg_action_count":   sum(r.action_count for r in records) / len(records),
            "avg_replan_count":   sum(r.replan_count for r in records) / len(records),
            "avg_duration_s":     sum(r.duration_s for r in records) / len(records),
            "executor_usage":     self.executor_usage(),
        }

    # ── Internal ──────────────────────────────────────────────────────

    def _all(self) -> list[PlanRecord]:
        self._ensure_loaded()
        with self._lock:
            return list(self._cache)

    def _append(self, rec: PlanRecord) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "session_id":     rec.session_id,
            "objective_goal": rec.objective_goal,
            "complexity":     rec.complexity,
            "action_count":   rec.action_count,
            "layer_count":    rec.layer_count,
            "worker_count":   rec.worker_count,
            "success":        rec.success,
            "duration_s":     rec.duration_s,
            "replan_count":   rec.replan_count,
            "executors_used": rec.executors_used,
            "timestamp":      rec.timestamp,
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
                        d = json.loads(line)
                        self._cache.append(PlanRecord(**d))
                    except Exception:
                        pass
            except FileNotFoundError:
                pass
            self._loaded = True
