"""Action Memory — stores outcomes so the executor selector can learn.

Every completed action is persisted to `.kryth/action_memory.jsonl`.
The ExecutorSelector queries success rates per (executor, goal_category)
to bias future selection toward historically reliable executors.

Records:
  action_id, goal, executor_used, success, duration_ms, verified,
  rolled_back, error, timestamp
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agent.action.action import Action, ActionResult

logger = logging.getLogger(__name__)

_MEMORY_FILE = ".kryth/action_memory.jsonl"
_MAX_RECORDS = 10_000   # rotate after this many entries


@dataclass
class ActionRecord:
    action_id: str
    goal: str
    executor_used: str
    success: bool
    verified: bool
    rolled_back: bool
    duration_ms: float
    error: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # Derived category: first 3 words of goal lowercased, used for rate lookups
    @property
    def goal_category(self) -> str:
        return " ".join(self.goal.lower().split()[:3])


class ActionMemory:
    """Persist and query action outcomes for learning-based executor selection."""

    def __init__(self, project_root: str | Path = ".") -> None:
        self._root = Path(project_root).resolve()
        self._file = self._root / _MEMORY_FILE
        self._lock = threading.Lock()
        # In-memory cache: (executor, goal_category) → [success_bool, ...]
        self._cache: dict[tuple[str, str], list[bool]] = defaultdict(list)
        self._loaded = False

    # ── Write ─────────────────────────────────────────────────────────

    def record(self, action: Action, result: ActionResult) -> None:
        rec = ActionRecord(
            action_id=action.id,
            goal=action.goal,
            executor_used=result.executor_used,
            success=result.success,
            verified=result.verified,
            rolled_back=result.rolled_back,
            duration_ms=result.duration_ms,
            error=result.error[:500] if result.error else "",
        )
        self._append(rec)
        with self._lock:
            key = (rec.executor_used, rec.goal_category)
            self._cache[key].append(rec.success)

    # ── Query ─────────────────────────────────────────────────────────

    def success_rate(self, executor_name: str, goal: str) -> float:
        """Return historical success rate (0–1) or -1.0 if no history."""
        self._ensure_loaded()
        category = " ".join(goal.lower().split()[:3])
        key = (executor_name, category)
        with self._lock:
            history = self._cache.get(key, [])
        if not history:
            return -1.0
        return sum(history) / len(history)

    def recent_records(self, n: int = 50) -> list[ActionRecord]:
        """Return the N most recent records."""
        self._ensure_loaded()
        records = []
        try:
            lines = self._file.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines[-n:]):
                try:
                    d = json.loads(line)
                    d.pop("goal_category", None)
                    records.append(ActionRecord(**d))
                except Exception:
                    pass
        except FileNotFoundError:
            pass
        return list(reversed(records))

    def executor_stats(self) -> dict[str, dict]:
        """Return per-executor aggregated stats."""
        self._ensure_loaded()
        stats: dict[str, dict] = {}
        with self._lock:
            for (executor, _), results in self._cache.items():
                if executor not in stats:
                    stats[executor] = {"total": 0, "success": 0, "rate": 0.0}
                stats[executor]["total"] += len(results)
                stats[executor]["success"] += sum(results)
        for ex, s in stats.items():
            s["rate"] = s["success"] / s["total"] if s["total"] else 0.0
        return stats

    # ── Internal ──────────────────────────────────────────────────────

    def _append(self, rec: ActionRecord) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "action_id": rec.action_id,
            "goal": rec.goal,
            "executor_used": rec.executor_used,
            "success": rec.success,
            "verified": rec.verified,
            "rolled_back": rec.rolled_back,
            "duration_ms": rec.duration_ms,
            "error": rec.error,
            "timestamp": rec.timestamp,
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
                        key = (d.get("executor_used", ""), " ".join(d.get("goal", "").lower().split()[:3]))
                        self._cache[key].append(bool(d.get("success", False)))
                    except Exception:
                        pass
            except FileNotFoundError:
                pass
            self._loaded = True
