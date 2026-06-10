from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ReflectionMetrics:
    total_reflections: int = 0
    total_lessons_extracted: int = 0
    total_lessons_promoted: int = 0
    total_lessons_rejected: int = 0
    total_lessons_filtered: int = 0
    total_repair_patterns_learned: int = 0
    total_workflows_learned: int = 0
    total_decisions_learned: int = 0
    avg_reflection_time_sec: float = 0.0
    avg_lessons_per_reflection: float = 0.0
    promotion_rate: float = 0.0
    noise_filter_rate: float = 0.0
    repair_learning_rate: float = 0.0
    workflow_learning_rate: float = 0.0
    agent_optimization_rate: float = 0.0


class ReflectionAnalytics:
    """
    Tracks what the Reflection Engine has learned over time.
    SQLite at ~/.kryth/reflection/analytics.db
    """

    def __init__(self, project_hash: str = "") -> None:
        self.project_hash = project_hash
        self._lock = threading.Lock()
        self._db_path = self._resolve_path(project_hash)
        self._init_db()

        self._counters: Dict[str, float] = {
            "reflections": 0,
            "lessons_extracted": 0,
            "lessons_promoted": 0,
            "lessons_rejected": 0,
            "lessons_filtered": 0,
            "repair_learned": 0,
            "workflows_learned": 0,
            "decisions_learned": 0,
            "total_reflection_time": 0.0,
        }

    def record_reflection(self, output: "ReflectionOutput") -> None:  # noqa: F821
        """Record a completed reflection's outcomes."""
        with self._lock:
            from agent.reflection.experience_record import LessonKind
            self._counters["reflections"] += 1
            self._counters["lessons_extracted"] += output.lesson_count
            self._counters["lessons_promoted"] += output.promoted_count
            self._counters["lessons_rejected"] += len(output.rejected_keys)
            self._counters["lessons_filtered"] += len(output.filtered_keys)
            self._counters["total_reflection_time"] += output.reflection_time_sec

            for lesson in output.lessons:
                if lesson.kind == LessonKind.REPAIR:
                    self._counters["repair_learned"] += 1
                elif lesson.kind == LessonKind.WORKFLOW:
                    self._counters["workflows_learned"] += 1
                elif lesson.kind == LessonKind.DECISION:
                    self._counters["decisions_learned"] += 1

            self._persist_event("reflection_complete", {
                "task_id": output.task_id,
                "lesson_count": output.lesson_count,
                "promoted": output.promoted_count,
                "reflection_time": output.reflection_time_sec,
            })

    def get_metrics(self) -> ReflectionMetrics:
        with self._lock:
            n = max(1, self._counters["reflections"])
            extracted = max(1, self._counters["lessons_extracted"])
            return ReflectionMetrics(
                total_reflections=int(self._counters["reflections"]),
                total_lessons_extracted=int(self._counters["lessons_extracted"]),
                total_lessons_promoted=int(self._counters["lessons_promoted"]),
                total_lessons_rejected=int(self._counters["lessons_rejected"]),
                total_lessons_filtered=int(self._counters["lessons_filtered"]),
                total_repair_patterns_learned=int(self._counters["repair_learned"]),
                total_workflows_learned=int(self._counters["workflows_learned"]),
                total_decisions_learned=int(self._counters["decisions_learned"]),
                avg_reflection_time_sec=self._counters["total_reflection_time"] / n,
                avg_lessons_per_reflection=self._counters["lessons_extracted"] / n,
                promotion_rate=self._counters["lessons_promoted"] / extracted,
                noise_filter_rate=self._counters["lessons_filtered"] / extracted,
                repair_learning_rate=self._counters["repair_learned"] / n,
                workflow_learning_rate=self._counters["workflows_learned"] / n,
                agent_optimization_rate=self._counters["decisions_learned"] / n,
            )

    def get_dashboard(self) -> Dict[str, Any]:
        m = self.get_metrics()
        return {
            "project_hash": self.project_hash,
            "generated_at": time.time(),
            "totals": {
                "reflections": m.total_reflections,
                "lessons_extracted": m.total_lessons_extracted,
                "lessons_promoted": m.total_lessons_promoted,
                "lessons_rejected": m.total_lessons_rejected,
                "lessons_filtered": m.total_lessons_filtered,
            },
            "learning_rates": {
                "repair_patterns": m.repair_learning_rate,
                "workflows": m.workflow_learning_rate,
                "decisions": m.agent_optimization_rate,
            },
            "efficiency": {
                "promotion_rate": m.promotion_rate,
                "noise_filter_rate": m.noise_filter_rate,
                "avg_reflection_time_sec": m.avg_reflection_time_sec,
                "avg_lessons_per_reflection": m.avg_lessons_per_reflection,
            },
        }

    def reset(self) -> None:
        with self._lock:
            for k in self._counters:
                self._counters[k] = 0

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self._db_path:
            return []
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT event_type, payload, recorded_at FROM events "
                    "WHERE project_hash=? ORDER BY recorded_at DESC LIMIT ?",
                    (self.project_hash, limit),
                ).fetchall()
            return [
                {"type": r["event_type"], "data": json.loads(r["payload"]), "at": r["recorded_at"]}
                for r in rows
            ]
        except Exception:
            return []

    def _resolve_path(self, project_hash: str) -> Optional[Path]:
        try:
            from agent.env import home_dir
            d = home_dir() / "reflection"
            d.mkdir(parents=True, exist_ok=True)
            return d / "analytics.db"
        except Exception:
            return None

    def _init_db(self) -> None:
        if not self._db_path:
            return
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        project_hash TEXT,
                        event_type TEXT,
                        payload TEXT,
                        recorded_at REAL
                    );
                """)
        except Exception:
            self._db_path = None

    def _persist_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self._db_path:
            return
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute(
                    "INSERT INTO events (project_hash, event_type, payload, recorded_at) VALUES (?,?,?,?)",
                    (self.project_hash, event_type, json.dumps(payload, default=str), time.time()),
                )
        except Exception:
            pass


_analytics_instances: Dict[str, ReflectionAnalytics] = {}


def get_reflection_analytics(project_hash: str = "") -> ReflectionAnalytics:
    if project_hash not in _analytics_instances:
        _analytics_instances[project_hash] = ReflectionAnalytics(project_hash)
    return _analytics_instances[project_hash]
