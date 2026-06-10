"""WorkspaceModel — live, incremental project entity index.

Tracks files, directories, modules, classes, functions, API routes,
components, DB tables, services, and tests.

Reuses ``agent.repo_index`` for Python symbol extraction and fingerprinting.
Only rescans changed files (incremental via xxhash fingerprints).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from agent.mission.config import EntityKind, MISSIONS_DIR

logger = logging.getLogger(__name__)

_CREATE_ENTITIES = """
CREATE TABLE IF NOT EXISTS workspace_entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_hash TEXT   NOT NULL,
    kind        TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    path        TEXT    NOT NULL,
    parent_path TEXT    NOT NULL DEFAULT '',
    line_no     INTEGER NOT NULL DEFAULT 0,
    signature   TEXT    NOT NULL DEFAULT '',
    last_modified REAL  NOT NULL DEFAULT 0,
    first_seen  REAL    NOT NULL,
    metadata_json TEXT  NOT NULL DEFAULT '{}',
    UNIQUE(project_hash, kind, name, path)
);
"""

_CREATE_FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS workspace_fts
USING fts5(name, path, kind, content=workspace_entities, content_rowid=id);
"""

_CREATE_IDX = """
CREATE INDEX IF NOT EXISTS idx_ws_project ON workspace_entities(project_hash);
CREATE INDEX IF NOT EXISTS idx_ws_kind ON workspace_entities(project_hash, kind);
CREATE INDEX IF NOT EXISTS idx_ws_path ON workspace_entities(project_hash, path);
"""


class WorkspaceModel:
    """Incrementally-updated entity model for a project.

    Parameters
    ----------
    project_hash:
        12-char project identity.
    db_path:
        Override for tests.
    """

    def __init__(self, project_hash: str, db_path: Optional[Path] = None) -> None:
        self.project_hash = project_hash
        ws_dir = MISSIONS_DIR / "workspace"
        if db_path:
            self._db_path = db_path
        else:
            ws_dir.mkdir(parents=True, exist_ok=True)
            self._db_path = ws_dir / f"{project_hash}.db"
        self._init_db()

    # ------------------------------------------------------------------
    # Sync (incremental)
    # ------------------------------------------------------------------

    def sync(self, root: str) -> int:
        """Sync Python symbols from *root* into the workspace model.

        Uses ``repo_index.build_index()`` (which is already fingerprint-aware)
        and upserts only new/changed symbols. Returns count of entities upserted.
        """
        try:
            import agent.repo_index as ri
            count = ri.build_index(root)
            entries: list[tuple] = []
            now = time.time()
            # Collect definitions from the in-memory index
            for name, locs in ri._index.items():
                for path, lineno, kind in locs:
                    abs_path = str(Path(root) / path)
                    mtime = 0.0
                    try:
                        mtime = os.path.getmtime(abs_path)
                    except OSError:
                        pass
                    entries.append((
                        self.project_hash,
                        _map_kind(kind),
                        name,
                        path,
                        str(Path(path).parent),
                        lineno,
                        "",
                        mtime,
                        now,
                        "{}",
                    ))

            if entries:
                with self._connect() as con:
                    con.executemany(
                        "INSERT OR REPLACE INTO workspace_entities"
                        "(project_hash,kind,name,path,parent_path,line_no,signature,last_modified,first_seen,metadata_json)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        entries,
                    )
            return len(entries)
        except Exception as exc:
            logger.warning("WorkspaceModel.sync failed: %s", exc)
            return 0

    def upsert_entity(
        self,
        kind: str,
        name: str,
        path: str,
        *,
        parent_path: str = "",
        line_no: int = 0,
        signature: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Manually register an entity (e.g. an API route discovered during browser testing)."""
        now = time.time()
        mtime = 0.0
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            pass
        with self._connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO workspace_entities"
                "(project_hash,kind,name,path,parent_path,line_no,signature,last_modified,first_seen,metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.project_hash, kind, name, path, parent_path,
                 line_no, signature, mtime, now, json.dumps(metadata or {})),
            )

    def mark_dirty(self, path: str, root: str = ".") -> None:
        """Invalidate *path* in the workspace model and trigger a re-sync of that file."""
        try:
            import agent.repo_index as ri
            ri.invalidate(path)
            ri._reindex_paths([Path(path)], root)
        except Exception as exc:
            logger.debug("WorkspaceModel.mark_dirty: %s", exc)
        # Remove stale entries for this path
        with self._connect() as con:
            con.execute(
                "DELETE FROM workspace_entities WHERE project_hash=? AND path=?",
                (self.project_hash, path),
            )

    def remove_entity(self, path: str) -> None:
        with self._connect() as con:
            con.execute(
                "DELETE FROM workspace_entities WHERE project_hash=? AND path=?",
                (self.project_hash, path),
            )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def query(
        self,
        kind: str | None = None,
        name_pattern: str | None = None,
        path_prefix: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        sql = "SELECT id,kind,name,path,parent_path,line_no,signature,last_modified,metadata_json FROM workspace_entities WHERE project_hash=?"
        params: list[Any] = [self.project_hash]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if name_pattern:
            sql += " AND name LIKE ?"
            params.append(f"%{name_pattern}%")
        if path_prefix:
            sql += " AND path LIKE ?"
            params.append(f"{path_prefix}%")
        sql += f" ORDER BY kind,path LIMIT {limit}"
        with self._connect() as con:
            rows = con.execute(sql, params).fetchall()
        return [_entity_row(r) for r in rows]

    def get_tree(self, root_path: str = "") -> dict:
        """Return entities grouped by parent_path (for display)."""
        entities = self.query(path_prefix=root_path or "")
        tree: dict[str, list] = {}
        for e in entities:
            parent = e["parent_path"] or "."
            tree.setdefault(parent, []).append({"name": e["name"], "kind": e["kind"], "path": e["path"]})
        return tree

    def stats(self) -> dict:
        with self._connect() as con:
            total = con.execute(
                "SELECT COUNT(*) FROM workspace_entities WHERE project_hash=?",
                (self.project_hash,)
            ).fetchone()[0]
            by_kind = con.execute(
                "SELECT kind, COUNT(*) FROM workspace_entities WHERE project_hash=? GROUP BY kind",
                (self.project_hash,)
            ).fetchall()
        return {"total": total, "by_kind": {k: n for k, n in by_kind}}

    def clear(self) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM workspace_entities WHERE project_hash=?", (self.project_hash,))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(_CREATE_ENTITIES)
            try:
                con.execute(_CREATE_FTS)
            except Exception:
                pass
            for stmt in _CREATE_IDX.strip().split(";"):
                if stmt.strip():
                    con.execute(stmt)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))


def _map_kind(repo_kind: str) -> str:
    mapping = {
        "function": EntityKind.FUNCTION.value,
        "class": EntityKind.CLASS.value,
        "method": EntityKind.FUNCTION.value,
        "import": EntityKind.MODULE.value,
    }
    return mapping.get(repo_kind, EntityKind.FILE.value)


def _entity_row(row: tuple) -> dict:
    return {
        "id": row[0], "kind": row[1], "name": row[2],
        "path": row[3], "parent_path": row[4],
        "line_no": row[5], "signature": row[6],
        "last_modified": row[7],
        "metadata": json.loads(row[8] or "{}"),
    }
