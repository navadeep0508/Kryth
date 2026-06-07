"""BacklogManager — persistent engineering backlog (Epic → Story → Task → Subtask).

Stored in SQLite. Reuses MissionManager for mission-level tracking.
Never duplicates memory systems — backlog items are linked to mission task nodes
via mission_id + node_id foreign keys.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.factory.config import (
    BACKLOG_DB_DIR, EpicStatus, StoryStatus, TaskStatus,
)

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS epics (
    id          TEXT PRIMARY KEY,
    project_hash TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    priority    INTEGER NOT NULL DEFAULT 50,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS stories (
    id          TEXT PRIMARY KEY,
    epic_id     TEXT NOT NULL REFERENCES epics(id),
    project_hash TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'backlog',
    priority    INTEGER NOT NULL DEFAULT 50,
    story_points INTEGER NOT NULL DEFAULT 1,
    assigned_to TEXT NOT NULL DEFAULT '',
    mission_node_id TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS tasks (
    id          TEXT PRIMARY KEY,
    story_id    TEXT NOT NULL REFERENCES stories(id),
    project_hash TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'todo',
    priority    INTEGER NOT NULL DEFAULT 50,
    depends_on_json TEXT NOT NULL DEFAULT '[]',
    assigned_to TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_epics_proj ON epics(project_hash);
CREATE INDEX IF NOT EXISTS idx_stories_epic ON stories(epic_id);
CREATE INDEX IF NOT EXISTS idx_tasks_story ON tasks(story_id);
"""


class BacklogManager:
    """Persistent engineering backlog for a project.

    Parameters
    ----------
    project_hash:
        12-char project identity.
    db_path:
        Override database path (for tests).
    """

    def __init__(self, project_hash: str, db_path: Optional[Path] = None) -> None:
        self.project_hash = project_hash
        if db_path:
            self._db_path = db_path
        else:
            BACKLOG_DB_DIR.mkdir(parents=True, exist_ok=True)
            self._db_path = BACKLOG_DB_DIR / f"{project_hash}.db"
        self._init_db()

    # ------------------------------------------------------------------
    # Epics
    # ------------------------------------------------------------------

    def add_epic(
        self,
        title: str,
        description: str = "",
        priority: int = 50,
        metadata: dict | None = None,
    ) -> str:
        eid = _uid()
        now = time.time()
        with self._con() as con:
            con.execute(
                "INSERT INTO epics(id,project_hash,title,description,status,priority,created_at,updated_at,metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (eid, self.project_hash, title, description,
                 EpicStatus.OPEN.value, priority, now, now,
                 json.dumps(metadata or {})),
            )
        return eid

    def get_epic(self, epic_id: str) -> dict | None:
        with self._con() as con:
            row = con.execute(
                "SELECT id,title,description,status,priority,created_at,updated_at,metadata_json"
                " FROM epics WHERE id=?", (epic_id,)
            ).fetchone()
        return _epic_row(row) if row else None

    def list_epics(self, status: str | None = None) -> list[dict]:
        sql = "SELECT id,title,description,status,priority,created_at,updated_at,metadata_json FROM epics WHERE project_hash=?"
        params: list = [self.project_hash]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY priority DESC, created_at"
        with self._con() as con:
            rows = con.execute(sql, params).fetchall()
        return [_epic_row(r) for r in rows]

    def update_epic_status(self, epic_id: str, status: EpicStatus) -> None:
        with self._con() as con:
            con.execute(
                "UPDATE epics SET status=?, updated_at=? WHERE id=?",
                (status.value, time.time(), epic_id),
            )

    # ------------------------------------------------------------------
    # Stories
    # ------------------------------------------------------------------

    def add_story(
        self,
        epic_id: str,
        title: str,
        description: str = "",
        priority: int = 50,
        story_points: int = 1,
        metadata: dict | None = None,
    ) -> str:
        sid = _uid()
        now = time.time()
        with self._con() as con:
            con.execute(
                "INSERT INTO stories(id,epic_id,project_hash,title,description,status,priority,"
                "story_points,assigned_to,mission_node_id,created_at,updated_at,metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, epic_id, self.project_hash, title, description,
                 StoryStatus.BACKLOG.value, priority, story_points,
                 "", "", now, now, json.dumps(metadata or {})),
            )
        return sid

    def get_story(self, story_id: str) -> dict | None:
        with self._con() as con:
            row = con.execute(
                "SELECT id,epic_id,title,description,status,priority,story_points,"
                "assigned_to,mission_node_id,created_at,updated_at,metadata_json"
                " FROM stories WHERE id=?", (story_id,)
            ).fetchone()
        return _story_row(row) if row else None

    def list_stories(self, epic_id: str | None = None, status: str | None = None) -> list[dict]:
        sql = ("SELECT id,epic_id,title,description,status,priority,story_points,"
               "assigned_to,mission_node_id,created_at,updated_at,metadata_json"
               " FROM stories WHERE project_hash=?")
        params: list = [self.project_hash]
        if epic_id:
            sql += " AND epic_id=?"
            params.append(epic_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY priority DESC, created_at"
        with self._con() as con:
            rows = con.execute(sql, params).fetchall()
        return [_story_row(r) for r in rows]

    def update_story_status(self, story_id: str, status: StoryStatus, assigned_to: str = "") -> None:
        now = time.time()
        with self._con() as con:
            if assigned_to:
                con.execute(
                    "UPDATE stories SET status=?, assigned_to=?, updated_at=? WHERE id=?",
                    (status.value, assigned_to, now, story_id),
                )
            else:
                con.execute(
                    "UPDATE stories SET status=?, updated_at=? WHERE id=?",
                    (status.value, now, story_id),
                )

    def link_story_to_mission_node(self, story_id: str, mission_node_id: str) -> None:
        with self._con() as con:
            con.execute(
                "UPDATE stories SET mission_node_id=?, updated_at=? WHERE id=?",
                (mission_node_id, time.time(), story_id),
            )

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def add_task(
        self,
        story_id: str,
        title: str,
        description: str = "",
        priority: int = 50,
        depends_on: list[str] | None = None,
        metadata: dict | None = None,
    ) -> str:
        tid = _uid()
        now = time.time()
        with self._con() as con:
            con.execute(
                "INSERT INTO tasks(id,story_id,project_hash,title,description,status,priority,"
                "depends_on_json,assigned_to,created_at,updated_at,metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, story_id, self.project_hash, title, description,
                 TaskStatus.TODO.value, priority,
                 json.dumps(depends_on or []), "",
                 now, now, json.dumps(metadata or {})),
            )
        return tid

    def get_task(self, task_id: str) -> dict | None:
        with self._con() as con:
            row = con.execute(
                "SELECT id,story_id,title,description,status,priority,depends_on_json,"
                "assigned_to,created_at,updated_at,metadata_json"
                " FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        return _task_row(row) if row else None

    def list_tasks(self, story_id: str, status: str | None = None) -> list[dict]:
        sql = ("SELECT id,story_id,title,description,status,priority,depends_on_json,"
               "assigned_to,created_at,updated_at,metadata_json"
               " FROM tasks WHERE story_id=?")
        params: list = [story_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY priority DESC, created_at"
        with self._con() as con:
            rows = con.execute(sql, params).fetchall()
        return [_task_row(r) for r in rows]

    def update_task_status(self, task_id: str, status: TaskStatus) -> None:
        with self._con() as con:
            con.execute(
                "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                (status.value, time.time(), task_id),
            )

    # ------------------------------------------------------------------
    # Stats / summary
    # ------------------------------------------------------------------

    def backlog_summary(self) -> dict[str, Any]:
        with self._con() as con:
            epic_count = con.execute(
                "SELECT COUNT(*) FROM epics WHERE project_hash=?", (self.project_hash,)
            ).fetchone()[0]
            story_counts = con.execute(
                "SELECT status, COUNT(*) FROM stories WHERE project_hash=? GROUP BY status",
                (self.project_hash,),
            ).fetchall()
            task_counts = con.execute(
                "SELECT status, COUNT(*) FROM tasks WHERE project_hash=? GROUP BY status",
                (self.project_hash,),
            ).fetchall()
        return {
            "epics": epic_count,
            "stories": {s: n for s, n in story_counts},
            "tasks": {s: n for s, n in task_counts},
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._con() as con:
            for stmt in _DDL.strip().split(";"):
                if stmt.strip():
                    con.execute(stmt)

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:12]

def _epic_row(r: tuple) -> dict:
    return {"id": r[0], "title": r[1], "description": r[2], "status": r[3],
            "priority": r[4], "created_at": r[5], "updated_at": r[6],
            "metadata": json.loads(r[7] or "{}")}

def _story_row(r: tuple) -> dict:
    return {"id": r[0], "epic_id": r[1], "title": r[2], "description": r[3],
            "status": r[4], "priority": r[5], "story_points": r[6],
            "assigned_to": r[7], "mission_node_id": r[8],
            "created_at": r[9], "updated_at": r[10],
            "metadata": json.loads(r[11] or "{}")}

def _task_row(r: tuple) -> dict:
    return {"id": r[0], "story_id": r[1], "title": r[2], "description": r[3],
            "status": r[4], "priority": r[5],
            "depends_on": json.loads(r[6] or "[]"),
            "assigned_to": r[7], "created_at": r[8], "updated_at": r[9],
            "metadata": json.loads(r[10] or "{}")}
