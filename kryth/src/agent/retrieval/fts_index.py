"""SQLite FTS5 full-text index for fast local code search.

Indexes file content with BM25 ranking via SQLite's built-in FTS5 engine.
Supports incremental updates — only changed files are re-indexed based on
mtime comparisons, so repeated searches don't pay full rebuild cost.

Database location: ~/.kryth/fts/<project_hash>.db
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import List, Optional, Tuple

from agent.retrieval import config as cfg
from agent.retrieval.fd_discovery import discover_files


def _home_dir() -> Path:
    try:
        from agent.env import home_dir
        return home_dir()
    except ImportError:
        return Path.home() / ".kryth"


_INDEX_DIR = _home_dir() / "fts"

_SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    mtime       REAL    NOT NULL,
    size        INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_content USING fts5(
    path    UNINDEXED,
    content,
    tokenize='unicode61 remove_diacritics 1'
);

CREATE INDEX IF NOT EXISTS idx_files_path  ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_mtime ON files(mtime);
"""


def _sanitize_fts_query(query: str) -> str:
    """Convert a plain-text query to safe FTS5 match syntax.

    FTS5 understands AND/OR/NOT and prefix '*'; everything else
    is treated as raw tokens. We strip punctuation that breaks the
    parser and append '*' so partial-word matches work.
    """
    # If the caller already used FTS operators, pass through
    if re.search(r"\b(AND|OR|NOT)\b", query):
        # Still sanitise dangerous chars
        query = re.sub(r'[():\^@]+', " ", query)
        return query.strip()

    # Extract word tokens and create prefix searches
    words = re.findall(r"[A-Za-z0-9_\-]+", query)
    if not words:
        # Return a safe no-op that won't crash FTS5
        return '""'
    return " ".join(f"{w}*" for w in words)


class FTSIndex:
    """SQLite FTS5 index for a single project directory."""

    def __init__(self, directory: str = ".") -> None:
        self._dir = os.path.abspath(directory)
        self._db_path = str(self._index_path())
        self._conn: Optional[sqlite3.Connection] = None

    def _project_hash(self) -> str:
        return hashlib.md5(self._dir.encode(), usedforsecurity=False).hexdigest()[:12]

    def _index_path(self) -> Path:
        _INDEX_DIR.mkdir(parents=True, exist_ok=True)
        return _INDEX_DIR / f"{self._project_hash()}.db"

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            # 64 MB page cache
            self._conn.execute("PRAGMA cache_size=-65536")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(_SCHEMA_SQL)
        row = self._conn.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._conn.commit()

    def _needs_update(self, conn: sqlite3.Connection, path: str, mtime: float) -> bool:
        row = conn.execute(
            "SELECT mtime FROM files WHERE path = ?", (path,)
        ).fetchone()
        if row is None:
            return True
        return float(row["mtime"]) < mtime

    def _index_file(
        self, conn: sqlite3.Connection, path: str, mtime: float, size: int
    ) -> None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                # Cap per-file content so huge generated files don't
                # blow up the index; 512 KB is plenty for search relevance
                content = f.read(512 * 1024)
        except OSError:
            return

        # Remove stale entries
        conn.execute("DELETE FROM fts_content WHERE path = ?", (path,))
        conn.execute("DELETE FROM files WHERE path = ?", (path,))

        conn.execute(
            "INSERT OR REPLACE INTO files (path, mtime, size) VALUES (?, ?, ?)",
            (path, mtime, size),
        )
        conn.execute(
            "INSERT INTO fts_content (path, content) VALUES (?, ?)",
            (path, content),
        )

    def build(self, batch_size: Optional[int] = None) -> int:
        """Build or incrementally refresh the index.

        Returns the count of files that were newly indexed.
        """
        if not cfg.ENABLE_FTS:
            return 0

        batch_size = batch_size or cfg.INDEX_BATCH_SIZE
        conn = self._connect()
        paths = discover_files(self._dir)

        indexed = 0
        batch: list[tuple[str, float, int]] = []

        for path in paths:
            try:
                st = os.stat(path)
            except OSError:
                continue
            if self._needs_update(conn, path, st.st_mtime):
                batch.append((path, st.st_mtime, st.st_size))
            if len(batch) >= batch_size:
                indexed += self._flush_batch(conn, batch)
                batch.clear()

        if batch:
            indexed += self._flush_batch(conn, batch)

        conn.execute(
            "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('last_build', ?)",
            (str(time.time()),),
        )
        conn.commit()
        return indexed

    def _flush_batch(
        self, conn: sqlite3.Connection, batch: list[tuple[str, float, int]]
    ) -> int:
        with conn:
            for path, mtime, size in batch:
                self._index_file(conn, path, mtime, size)
        return len(batch)

    def update(self, paths: List[str]) -> int:
        """Incrementally update index for specific paths (add/modify/delete).

        Returns the count of files updated.
        """
        if not cfg.ENABLE_FTS:
            return 0

        conn = self._connect()
        updated = 0

        with conn:
            for path in paths:
                if not os.path.isfile(path):
                    # Deleted file — evict from index
                    conn.execute(
                        "DELETE FROM fts_content WHERE path = ?", (path,)
                    )
                    conn.execute("DELETE FROM files WHERE path = ?", (path,))
                    updated += 1
                    continue

                try:
                    st = os.stat(path)
                except OSError:
                    continue

                if self._needs_update(conn, path, st.st_mtime):
                    self._index_file(conn, path, st.st_mtime, st.st_size)
                    updated += 1

        return updated

    def search(
        self, query: str, limit: int = 20
    ) -> List[Tuple[str, float, str]]:
        """Search the FTS5 index.

        Returns ``[(path, relevance_score, snippet), ...]``.
        *relevance_score* is the absolute value of the FTS5 rank (higher = better).
        Empty list if the index hasn't been built yet.
        """
        if not cfg.ENABLE_FTS:
            return []

        conn = self._connect()
        fts_query = _sanitize_fts_query(query)

        try:
            rows = conn.execute(
                """
                SELECT
                    path,
                    rank,
                    snippet(fts_content, 1, '[', ']', '...', 24) AS snippet
                FROM fts_content
                WHERE fts_content MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Index empty or FTS5 unavailable
            return []

        results: List[Tuple[str, float, str]] = []
        for row in rows:
            path = row["path"] if "path" in row.keys() else row[0]
            rank = abs(float(row["rank"] if "rank" in row.keys() else row[1]))
            snippet = row["snippet"] if "snippet" in row.keys() else row[2]
            if path:
                results.append((path, rank, snippet))

        return results

    def stats(self) -> dict:
        """Return index statistics."""
        try:
            conn = self._connect()
            file_count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            last_build_row = conn.execute(
                "SELECT value FROM schema_meta WHERE key='last_build'"
            ).fetchone()
            last_build = float(last_build_row[0]) if last_build_row else 0.0
            return {
                "indexed_files": file_count,
                "last_build": last_build,
                "db_path": self._db_path,
                "directory": self._dir,
            }
        except Exception as e:
            return {"error": str(e)}

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_instances: dict[str, FTSIndex] = {}


def get_index(directory: str = ".") -> FTSIndex:
    """Return the FTS index for *directory*, creating it if needed."""
    key = os.path.abspath(directory)
    if key not in _instances:
        _instances[key] = FTSIndex(directory)
    return _instances[key]


def search(
    query: str, directory: str = ".", limit: int = 20
) -> List[Tuple[str, float, str]]:
    """Convenience wrapper: search the FTS index, auto-building if empty."""
    idx = get_index(directory)
    stats = idx.stats()
    if stats.get("indexed_files", 0) == 0 and not stats.get("error"):
        idx.build()
    return idx.search(query, limit=limit)
