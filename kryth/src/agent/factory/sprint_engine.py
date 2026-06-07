"""SprintEngine — execute work in Plan → Build → Test → Validate → Review → Learn iterations.

Reuses:
- MissionManager for DAG persistence and task tracking
- ExecutionSupervisor for per-step oversight
- ReflectionEngine for post-sprint learning
- BacklogManager for story state transitions
- CheckpointEngine via MissionManager.checkpoint()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.factory.config import (
    SPRINTS_DB_DIR, SprintStatus, StoryStatus, TaskStatus,
)

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS sprints (
    id          TEXT PRIMARY KEY,
    project_hash TEXT NOT NULL,
    name        TEXT NOT NULL,
    goal        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'planned',
    mission_id  TEXT NOT NULL DEFAULT '',
    started_at  REAL,
    completed_at REAL,
    created_at  REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sprint_stories (
    sprint_id   TEXT NOT NULL,
    story_id    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'backlog',
    PRIMARY KEY (sprint_id, story_id)
);
CREATE INDEX IF NOT EXISTS idx_sprint_proj ON sprints(project_hash);
"""


@dataclass
class SprintResult:
    sprint_id: str
    name: str
    status: str
    stories_completed: list[str] = field(default_factory=list)
    stories_failed: list[str] = field(default_factory=list)
    test_results: dict = field(default_factory=dict)
    review_findings: list[dict] = field(default_factory=list)
    lessons_learned: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "sprint_id": self.sprint_id,
            "name": self.name,
            "status": self.status,
            "stories_completed": self.stories_completed,
            "stories_failed": self.stories_failed,
            "test_results": self.test_results,
            "review_findings": self.review_findings,
            "lessons_learned": self.lessons_learned,
            "duration_seconds": self.duration_seconds,
        }


class SprintEngine:
    """Execute autonomous engineering sprints.

    Each sprint cycles through: Plan → Build → Test → Browser Validate → Review → Learn.

    Parameters
    ----------
    project_hash:
        Project identity.
    mission_manager:
        MissionManager instance for DAG tracking.
    backlog:
        BacklogManager instance for story state transitions.
    db_path:
        Override SQLite path (for tests).
    """

    def __init__(
        self,
        project_hash: str,
        mission_manager: Any = None,
        backlog: Any = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.project_hash = project_hash
        self._mm = mission_manager
        self._backlog = backlog
        if db_path:
            self._db_path = db_path
        else:
            SPRINTS_DB_DIR.mkdir(parents=True, exist_ok=True)
            self._db_path = SPRINTS_DB_DIR / f"{project_hash}.db"
        self._init_db()

    # ------------------------------------------------------------------
    # Sprint CRUD
    # ------------------------------------------------------------------

    def create_sprint(
        self,
        name: str,
        goal: str = "",
        story_ids: list[str] | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Create and return a sprint id."""
        sid = str(uuid.uuid4())[:12]
        now = time.time()
        with self._con() as con:
            con.execute(
                "INSERT INTO sprints(id,project_hash,name,goal,status,created_at,metadata_json)"
                " VALUES(?,?,?,?,?,?,?)",
                (sid, self.project_hash, name, goal,
                 SprintStatus.PLANNED.value, now, json.dumps(metadata or {})),
            )
            for story_id in (story_ids or []):
                con.execute(
                    "INSERT OR IGNORE INTO sprint_stories(sprint_id,story_id,status) VALUES(?,?,?)",
                    (sid, story_id, "backlog"),
                )
        return sid

    def get_sprint(self, sprint_id: str) -> dict | None:
        with self._con() as con:
            row = con.execute(
                "SELECT id,name,goal,status,mission_id,started_at,completed_at,created_at,metadata_json"
                " FROM sprints WHERE id=?", (sprint_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "name": row[1], "goal": row[2], "status": row[3],
            "mission_id": row[4], "started_at": row[5], "completed_at": row[6],
            "created_at": row[7], "metadata": json.loads(row[8] or "{}"),
        }

    def list_sprints(self, status: str | None = None) -> list[dict]:
        sql = "SELECT id,name,goal,status,mission_id,started_at,completed_at,created_at,metadata_json FROM sprints WHERE project_hash=?"
        params: list = [self.project_hash]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        with self._con() as con:
            rows = con.execute(sql, params).fetchall()
        return [{"id": r[0], "name": r[1], "goal": r[2], "status": r[3],
                 "mission_id": r[4], "started_at": r[5], "completed_at": r[6],
                 "created_at": r[7], "metadata": json.loads(r[8] or "{}")}
                for r in rows]

    def get_sprint_stories(self, sprint_id: str) -> list[dict]:
        with self._con() as con:
            rows = con.execute(
                "SELECT story_id, status FROM sprint_stories WHERE sprint_id=?",
                (sprint_id,),
            ).fetchall()
        return [{"story_id": r[0], "status": r[1]} for r in rows]

    # ------------------------------------------------------------------
    # Sprint execution (lightweight coordination layer)
    # ------------------------------------------------------------------

    def start(self, sprint_id: str) -> str:
        """Start the sprint: create a mission, set status to active.

        Returns the mission_id.
        """
        sprint = self.get_sprint(sprint_id)
        if sprint is None:
            raise KeyError(f"Sprint {sprint_id!r} not found")

        mission_id = ""
        if self._mm is not None:
            stories = self.get_sprint_stories(sprint_id)
            tasks = [
                {
                    "id": f"story-{s['story_id']}",
                    "description": f"Implement story {s['story_id']}",
                    "depends_on": [],
                }
                for s in stories
            ]
            mission_id = self._mm.start(
                name=f"Sprint: {sprint['name']}",
                goal=sprint["goal"],
                tasks=tasks,
            )

        now = time.time()
        with self._con() as con:
            con.execute(
                "UPDATE sprints SET status=?, mission_id=?, started_at=? WHERE id=?",
                (SprintStatus.ACTIVE.value, mission_id, now, sprint_id),
            )
        logger.info("Sprint started: %s [mission=%s]", sprint["name"], mission_id)
        return mission_id

    def complete_story(self, sprint_id: str, story_id: str, success: bool = True) -> None:
        """Mark a story as done or failed within the sprint."""
        status = "done" if success else "failed"
        with self._con() as con:
            con.execute(
                "UPDATE sprint_stories SET status=? WHERE sprint_id=? AND story_id=?",
                (status, sprint_id, story_id),
            )
        if self._backlog is not None:
            new_status = StoryStatus.DONE if success else StoryStatus.BLOCKED
            self._backlog.update_story_status(story_id, new_status)

    def complete(self, sprint_id: str) -> SprintResult:
        """Close the sprint, reflect, and return a SprintResult."""
        sprint = self.get_sprint(sprint_id)
        if sprint is None:
            raise KeyError(f"Sprint {sprint_id!r} not found")

        stories = self.get_sprint_stories(sprint_id)
        done = [s["story_id"] for s in stories if s["status"] == "done"]
        failed = [s["story_id"] for s in stories if s["status"] == "failed"]

        now = time.time()
        with self._con() as con:
            con.execute(
                "UPDATE sprints SET status=?, completed_at=? WHERE id=?",
                (SprintStatus.COMPLETED.value, now, sprint_id),
            )

        # Complete associated mission
        if self._mm and sprint.get("mission_id"):
            self._mm.complete(sprint["mission_id"])

        # Post-sprint reflection
        lessons = self._reflect(sprint, done, failed)

        duration = (now - sprint.get("started_at", now))
        result = SprintResult(
            sprint_id=sprint_id,
            name=sprint["name"],
            status=SprintStatus.COMPLETED.value,
            stories_completed=done,
            stories_failed=failed,
            lessons_learned=lessons,
            duration_seconds=round(duration, 1),
        )
        logger.info("Sprint completed: %s (%d done, %d failed)", sprint["name"], len(done), len(failed))
        return result

    def abort(self, sprint_id: str, reason: str = "") -> None:
        with self._con() as con:
            con.execute(
                "UPDATE sprints SET status=? WHERE id=?",
                (SprintStatus.ABORTED.value, sprint_id),
            )
        if self._mm:
            sprint = self.get_sprint(sprint_id)
            if sprint and sprint.get("mission_id"):
                self._mm.abort(sprint["mission_id"], reason=reason)

    def progress(self, sprint_id: str) -> dict:
        stories = self.get_sprint_stories(sprint_id)
        total = len(stories)
        done = sum(1 for s in stories if s["status"] == "done")
        failed = sum(1 for s in stories if s["status"] == "failed")
        return {
            "total": total,
            "done": done,
            "failed": failed,
            "in_progress": total - done - failed,
            "percent": round(done / total * 100, 1) if total else 0,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _reflect(self, sprint: dict, done: list, failed: list) -> list[str]:
        lessons: list[str] = []
        try:
            from agent.reflection import get_reflection_engine
            engine = get_reflection_engine(project_hash=self.project_hash)
            from agent.reflection.experience_record import ReflectionInput
            inp = ReflectionInput(
                task_description=f"Sprint: {sprint['name']} — {sprint['goal']}",
                task_id=sprint["id"],
                project_hash=self.project_hash,
                final_output=f"Completed {len(done)} stories, failed {len(failed)}.",
            )
            output = engine.reflect(inp)
            lessons = [l.key for l in output.lessons[:5]]
        except Exception as exc:
            logger.debug("Sprint reflection failed: %s", exc)
        return lessons

    def _init_db(self) -> None:
        with self._con() as con:
            for stmt in _DDL.strip().split(";"):
                if stmt.strip():
                    con.execute(stmt)

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))
