"""ArtifactTracker — links produced/modified artifacts to mission tasks.

Every file write, API creation, schema migration, or component generation
should be recorded here so KRYTH can answer: "what did task X produce?"
and "which task last touched file Y?".
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from agent.mission.config import ArtifactKind, MISSIONS_DIR

logger = logging.getLogger(__name__)

_CREATE = """
CREATE TABLE IF NOT EXISTS artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  TEXT    NOT NULL,
    task_node_id TEXT   NOT NULL DEFAULT '',
    project_hash TEXT   NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'file',
    name        TEXT    NOT NULL DEFAULT '',
    path        TEXT    NOT NULL DEFAULT '',
    operation   TEXT    NOT NULL DEFAULT 'created',
    timestamp   REAL    NOT NULL,
    metadata_json TEXT  NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_art_mission ON artifacts(mission_id);
CREATE INDEX IF NOT EXISTS idx_art_path ON artifacts(project_hash, path);
CREATE INDEX IF NOT EXISTS idx_art_task ON artifacts(task_node_id);
"""


class ArtifactTracker:
    """Record and query artifacts produced by mission tasks.

    Parameters
    ----------
    project_hash:
        12-char project identity.
    db_path:
        Override for tests.
    """

    def __init__(self, project_hash: str, db_path: Optional[Path] = None) -> None:
        self.project_hash = project_hash
        art_dir = MISSIONS_DIR / "artifacts"
        if db_path:
            self._db_path = db_path
        else:
            art_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = art_dir / f"{project_hash}.db"
        self._init_db()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        mission_id: str,
        task_node_id: str,
        path: str,
        *,
        kind: str = ArtifactKind.FILE.value,
        name: str = "",
        operation: str = "created",
        metadata: dict | None = None,
    ) -> int:
        """Record an artifact operation. Returns the row id."""
        if not name:
            name = Path(path).name
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO artifacts(mission_id,task_node_id,project_hash,kind,name,path,operation,timestamp,metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (mission_id, task_node_id, self.project_hash,
                 kind, name, path, operation, time.time(),
                 json.dumps(metadata or {})),
            )
            return cur.lastrowid or 0

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def get_by_task(self, task_node_id: str) -> list[dict]:
        """All artifacts produced/modified by a specific task node."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,mission_id,task_node_id,kind,name,path,operation,timestamp,metadata_json"
                " FROM artifacts WHERE task_node_id=? ORDER BY timestamp",
                (task_node_id,),
            ).fetchall()
        return [_art_row(r) for r in rows]

    def get_by_path(self, path: str) -> list[dict]:
        """History of all operations on a specific file path."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,mission_id,task_node_id,kind,name,path,operation,timestamp,metadata_json"
                " FROM artifacts WHERE project_hash=? AND path=? ORDER BY timestamp DESC",
                (self.project_hash, path),
            ).fetchall()
        return [_art_row(r) for r in rows]

    def get_mission_artifacts(self, mission_id: str) -> dict[str, list[dict]]:
        """All artifacts for *mission_id*, grouped by kind."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id,mission_id,task_node_id,kind,name,path,operation,timestamp,metadata_json"
                " FROM artifacts WHERE mission_id=? ORDER BY kind,timestamp",
                (mission_id,),
            ).fetchall()
        grouped: dict[str, list] = {}
        for r in rows:
            art = _art_row(r)
            grouped.setdefault(art["kind"], []).append(art)
        return grouped

    def paths_modified_by_mission(self, mission_id: str) -> list[str]:
        """Unique file paths touched by any task in *mission_id*."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT DISTINCT path FROM artifacts WHERE mission_id=?",
                (mission_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def last_task_for_path(self, path: str) -> dict | None:
        """Return the most recent artifact record for *path*."""
        with self._connect() as con:
            row = con.execute(
                "SELECT id,mission_id,task_node_id,kind,name,path,operation,timestamp,metadata_json"
                " FROM artifacts WHERE project_hash=? AND path=? ORDER BY timestamp DESC LIMIT 1",
                (self.project_hash, path),
            ).fetchone()
        return _art_row(row) if row else None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as con:
            for stmt in _CREATE.strip().split(";"):
                if stmt.strip():
                    con.execute(stmt)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))


def _art_row(row: tuple) -> dict:
    return {
        "id": row[0], "mission_id": row[1], "task_node_id": row[2],
        "kind": row[3], "name": row[4], "path": row[5],
        "operation": row[6], "timestamp": row[7],
        "metadata": json.loads(row[8] or "{}"),
    }
