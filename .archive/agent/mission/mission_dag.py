"""MissionDAG — persisted wrapper around the in-memory TaskGraph.

Stores mission state in SQLite so KRYTH can resume after a restart.
Reuses the existing ``agent.task_graph.TaskGraph`` / ``TaskNode`` classes
for in-memory execution; this module adds the persistence layer only.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from agent.mission.config import MISSIONS_DIR, MissionStatus, NodeStatus

logger = logging.getLogger(__name__)

_CREATE_MISSIONS = """
CREATE TABLE IF NOT EXISTS missions (
    id          TEXT    PRIMARY KEY,
    project_hash TEXT   NOT NULL,
    name        TEXT    NOT NULL,
    goal        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    completed_at REAL,
    metadata_json TEXT  NOT NULL DEFAULT '{}'
);
"""

_CREATE_NODES = """
CREATE TABLE IF NOT EXISTS mission_nodes (
    id              TEXT    NOT NULL,
    mission_id      TEXT    NOT NULL REFERENCES missions(id),
    tool            TEXT    NOT NULL DEFAULT '',
    action          TEXT    NOT NULL DEFAULT '',
    params_json     TEXT    NOT NULL DEFAULT '{}',
    description     TEXT    NOT NULL DEFAULT '',
    depends_on_json TEXT    NOT NULL DEFAULT '[]',
    status          TEXT    NOT NULL DEFAULT 'pending',
    result_json     TEXT,
    error           TEXT    NOT NULL DEFAULT '',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    started_at      REAL,
    completed_at    REAL,
    PRIMARY KEY (id, mission_id)
);
"""

_CREATE_IDX = """
CREATE INDEX IF NOT EXISTS idx_nodes_mission ON mission_nodes(mission_id);
CREATE INDEX IF NOT EXISTS idx_missions_project ON missions(project_hash);
"""


class MissionDAG:
    """Persistent storage for mission task graphs.

    Parameters
    ----------
    project_hash:
        12-char project identity (from ``persistence.project_hash_for()``).
    db_path:
        Override database location (used in tests).
    """

    def __init__(self, project_hash: str, db_path: Optional[Path] = None) -> None:
        self.project_hash = project_hash
        if db_path:
            self._db_path = db_path
        else:
            MISSIONS_DIR.mkdir(parents=True, exist_ok=True)
            self._db_path = MISSIONS_DIR / f"{project_hash}.db"
        self._init_db()

    # ------------------------------------------------------------------
    # Mission CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        name: str,
        goal: str = "",
        metadata: dict | None = None,
        mission_id: str | None = None,
    ) -> str:
        """Create a new mission record. Returns the mission_id."""
        import uuid
        mid = mission_id or str(uuid.uuid4())[:12]
        now = time.time()
        with self._connect() as con:
            con.execute(
                "INSERT INTO missions(id,project_hash,name,goal,status,created_at,updated_at,metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (mid, self.project_hash, name, goal,
                 MissionStatus.PENDING.value, now, now,
                 json.dumps(metadata or {})),
            )
        logger.info("Mission created: %s (%s)", name, mid)
        return mid

    def get(self, mission_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT id,name,goal,status,created_at,updated_at,completed_at,metadata_json"
                " FROM missions WHERE id=?", (mission_id,)
            ).fetchone()
        if row is None:
            return None
        return self._mission_row(row)

    def list_missions(self, status: str | None = None) -> list[dict]:
        query = "SELECT id,name,goal,status,created_at,updated_at,completed_at,metadata_json FROM missions WHERE project_hash=?"
        params: list = [self.project_hash]
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC"
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [self._mission_row(r) for r in rows]

    def get_resumable(self) -> list[dict]:
        """Return missions in pending/active/paused state, most recent first."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,name,goal,status,created_at,updated_at,completed_at,metadata_json"
                " FROM missions WHERE project_hash=? AND status IN ('pending','active','paused')"
                " ORDER BY updated_at DESC",
                (self.project_hash,),
            ).fetchall()
        return [self._mission_row(r) for r in rows]

    def update_status(self, mission_id: str, status: MissionStatus, completed_at: float | None = None) -> None:
        now = time.time()
        with self._connect() as con:
            con.execute(
                "UPDATE missions SET status=?, updated_at=?, completed_at=COALESCE(?,completed_at) WHERE id=?",
                (status.value, now, completed_at, mission_id),
            )

    def update_metadata(self, mission_id: str, metadata: dict) -> None:
        now = time.time()
        with self._connect() as con:
            con.execute(
                "UPDATE missions SET metadata_json=?, updated_at=? WHERE id=?",
                (json.dumps(metadata), now, mission_id),
            )

    # ------------------------------------------------------------------
    # Node CRUD
    # ------------------------------------------------------------------

    def add_node(self, mission_id: str, node: dict) -> None:
        """Insert or update a task node for a mission."""
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO mission_nodes"
                "(id,mission_id,tool,action,params_json,description,depends_on_json,status)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (
                    node["id"], mission_id,
                    node.get("tool", ""),
                    node.get("action", ""),
                    json.dumps(node.get("params", {})),
                    node.get("description", ""),
                    json.dumps(node.get("depends_on", [])),
                    node.get("status", NodeStatus.PENDING.value),
                ),
            )

    def update_node(
        self,
        mission_id: str,
        node_id: str,
        status: str | None = None,
        result: Any = None,
        error: str = "",
        retry_count: int | None = None,
    ) -> None:
        updates: list[str] = []
        params: list = []
        if status is not None:
            updates.append("status=?")
            params.append(status)
            if status == NodeStatus.ACTIVE.value:
                updates.append("started_at=?")
                params.append(time.time())
            elif status in (NodeStatus.COMPLETED.value, NodeStatus.FAILED.value):
                updates.append("completed_at=?")
                params.append(time.time())
        if result is not None:
            updates.append("result_json=?")
            params.append(json.dumps(result))
        if error:
            updates.append("error=?")
            params.append(error)
        if retry_count is not None:
            updates.append("retry_count=?")
            params.append(retry_count)
        if not updates:
            return
        params.extend([node_id, mission_id])
        with self._connect() as con:
            con.execute(
                f"UPDATE mission_nodes SET {', '.join(updates)} WHERE id=? AND mission_id=?",
                params,
            )

    def get_nodes(self, mission_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,tool,action,params_json,description,depends_on_json,"
                "status,result_json,error,retry_count,started_at,completed_at"
                " FROM mission_nodes WHERE mission_id=?",
                (mission_id,),
            ).fetchall()
        return [self._node_row(r) for r in rows]

    def get_node(self, mission_id: str, node_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT id,tool,action,params_json,description,depends_on_json,"
                "status,result_json,error,retry_count,started_at,completed_at"
                " FROM mission_nodes WHERE id=? AND mission_id=?",
                (node_id, mission_id),
            ).fetchone()
        return self._node_row(row) if row else None

    def progress(self, mission_id: str) -> dict:
        """Return a progress summary dict."""
        nodes = self.get_nodes(mission_id)
        total = len(nodes)
        if total == 0:
            return {"total": 0, "completed": 0, "failed": 0, "active": 0, "pending": 0, "percent": 0}
        counts: dict[str, int] = {}
        for n in nodes:
            counts[n["status"]] = counts.get(n["status"], 0) + 1
        completed = counts.get(NodeStatus.COMPLETED.value, 0)
        return {
            "total": total,
            "completed": completed,
            "failed": counts.get(NodeStatus.FAILED.value, 0),
            "active": counts.get(NodeStatus.ACTIVE.value, 0),
            "pending": counts.get(NodeStatus.PENDING.value, 0),
            "blocked": counts.get(NodeStatus.BLOCKED.value, 0),
            "percent": round(completed / total * 100, 1) if total else 0,
        }

    # ------------------------------------------------------------------
    # TaskGraph integration
    # ------------------------------------------------------------------

    def to_task_graph(self, mission_id: str):
        """Reconstruct an in-memory TaskGraph from the persisted nodes."""
        try:
            from agent.task_graph import TaskGraph, TaskNode, TaskStatus
        except ImportError:
            return None
        nodes = self.get_nodes(mission_id)
        graph = TaskGraph()
        for n in nodes:
            status_map = {
                "pending": TaskStatus.PENDING,
                "active": TaskStatus.RUNNING,
                "completed": TaskStatus.SUCCESS,
                "failed": TaskStatus.FAILED,
                "blocked": TaskStatus.PENDING,
                "retrying": TaskStatus.RETRYING,
                "skipped": TaskStatus.SKIPPED,
            }
            node = TaskNode(
                id=n["id"],
                tool=n.get("tool", ""),
                action=n.get("action", ""),
                params=n.get("params", {}),
                description=n.get("description", ""),
                depends_on=n.get("depends_on", []),
                status=status_map.get(n["status"], TaskStatus.PENDING),
                result=n.get("result"),
                error=n.get("error", ""),
                retry_count=n.get("retry_count", 0),
            )
            graph.add_node(node)
        return graph

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(_CREATE_MISSIONS)
            con.execute(_CREATE_NODES)
            for stmt in _CREATE_IDX.strip().split(";"):
                if stmt.strip():
                    con.execute(stmt)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    @staticmethod
    def _mission_row(row: tuple) -> dict:
        return {
            "id": row[0], "name": row[1], "goal": row[2],
            "status": row[3], "created_at": row[4],
            "updated_at": row[5], "completed_at": row[6],
            "metadata": json.loads(row[7] or "{}"),
        }

    @staticmethod
    def _node_row(row: tuple) -> dict:
        result_raw = row[7]
        try:
            result = json.loads(result_raw) if result_raw else None
        except Exception:
            result = result_raw
        return {
            "id": row[0], "tool": row[1], "action": row[2],
            "params": json.loads(row[3] or "{}"),
            "description": row[4],
            "depends_on": json.loads(row[5] or "[]"),
            "status": row[6],
            "result": result,
            "error": row[8] or "",
            "retry_count": row[9] or 0,
            "started_at": row[10],
            "completed_at": row[11],
        }
