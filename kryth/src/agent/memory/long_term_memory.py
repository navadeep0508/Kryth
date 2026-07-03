"""LongTermMemory — persistent strategic memory across sessions.

Stored in SQLite at ~/.kryth/long_term.db.
This is the only persistent strategic memory in KRYTH.

Fronted by the existing add_memory tool and /memory REPL commands.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class LTMemoryEntry:
    id: int = 0
    scope: str = "project"  # "user" | "project" | "local"
    key: str = ""
    value: str = ""
    category: str = "note"  # "note" | "preference" | "convention" | "arch" | "heuristic"
    source: str = ""  # e.g. "add_memory", "episodic_compress"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    access_count: int = 0
    importance: float = 0.5


class LongTermMemory:
    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self._db_path = db_path
        else:
            home = Path.home() / ".kryth"
            home.mkdir(parents=True, exist_ok=True)
            self._db_path = str(home / "long_term.db")
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL DEFAULT 'project',
                key TEXT NOT NULL DEFAULT '',
                value TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'note',
                source TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                access_count INTEGER DEFAULT 0,
                importance REAL DEFAULT 0.5
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ltm_scope ON long_term_memory(scope)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ltm_key ON long_term_memory(key)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ltm_importance ON long_term_memory(importance DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ltm_category ON long_term_memory(category)
        """)
        # FTS5 virtual table for full-text search
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS long_term_memory_fts
            USING fts5(value, key, category, content='long_term_memory', content_rowid='id')
        """)
        conn.commit()

    def add(
        self,
        value: str,
        scope: str = "project",
        key: str = "",
        category: str = "note",
        source: str = "",
        importance: float = 0.5,
    ) -> int:
        now = time.time()
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO long_term_memory (scope, key, value, category, source, created_at, updated_at, importance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (scope, key, value, category, source, now, now, importance),
        )
        row_id = cursor.lastrowid or 0
        conn.execute(
            "INSERT INTO long_term_memory_fts (rowid, value, key, category) VALUES (?, ?, ?, ?)",
            (row_id, value, key, category),
        )
        conn.commit()
        return row_id

    def add_batch(self, entries: list[dict]) -> int:
        """Add multiple entries in one transaction. Each dict must have at least 'value'."""
        now = time.time()
        conn = self._get_conn()
        count = 0
        for e in entries:
            scope = e.get("scope", "project")
            key = e.get("key", "")
            value = e.get("value", "")
            category = e.get("category", "note")
            source = e.get("source", "")
            importance = e.get("importance", 0.5)
            cursor = conn.execute(
                """INSERT INTO long_term_memory (scope, key, value, category, source, created_at, updated_at, importance)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (scope, key, value, category, source, now, now, importance),
            )
            row_id = cursor.lastrowid or 0
            conn.execute(
                "INSERT INTO long_term_memory_fts (rowid, value, key, category) VALUES (?, ?, ?, ?)",
                (row_id, value, key, category),
            )
            count += 1
        conn.commit()
        return count

    def search_fts(self, query: str, limit: int = 10) -> list[LTMemoryEntry]:
        """Full-text search using FTS5."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT ltm.* FROM long_term_memory ltm
                   JOIN long_term_memory_fts fts ON ltm.id = fts.rowid
                   WHERE long_term_memory_fts MATCH ?
                   ORDER BY ltm.importance DESC, ltm.access_count DESC
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
            result = []
            for row in rows:
                entry = LTMemoryEntry(
                    id=row["id"], scope=row["scope"], key=row["key"],
                    value=row["value"], category=row["category"],
                    source=row["source"], created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    access_count=row["access_count"],
                    importance=row["importance"],
                )
                result.append(entry)
                conn.execute("UPDATE long_term_memory SET access_count = access_count + 1 WHERE id = ?", (entry.id,))
            conn.commit()
            return result
        except sqlite3.OperationalError:
            return self.search(query, limit)

    def search(self, query: str, limit: int = 10) -> list[LTMemoryEntry]:
        conn = self._get_conn()
        pattern = f"%{query}%"
        rows = conn.execute(
            """SELECT * FROM long_term_memory
               WHERE value LIKE ? OR key LIKE ?
               ORDER BY importance DESC, access_count DESC
               LIMIT ?""",
            (pattern, pattern, limit),
        ).fetchall()
        result = []
        for row in rows:
            entry = LTMemoryEntry(
                id=row["id"], scope=row["scope"], key=row["key"],
                value=row["value"], category=row["category"],
                source=row["source"], created_at=row["created_at"],
                updated_at=row["updated_at"],
                access_count=row["access_count"],
                importance=row["importance"],
            )
            result.append(entry)
            conn.execute("UPDATE long_term_memory SET access_count = access_count + 1 WHERE id = ?", (entry.id,))
        conn.commit()
        return result

    def get_recent(self, limit: int = 20) -> list[LTMemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM long_term_memory ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            LTMemoryEntry(
                id=r["id"], scope=r["scope"], key=r["key"],
                value=r["value"], category=r["category"],
                source=r["source"], created_at=r["created_at"],
                updated_at=r["updated_at"],
                access_count=r["access_count"],
                importance=r["importance"],
            )
            for r in rows
        ]

    def get_by_scope(self, scope: str, limit: int = 50) -> list[LTMemoryEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM long_term_memory WHERE scope = ? ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (scope, limit),
        ).fetchall()
        return [
            LTMemoryEntry(
                id=r["id"], scope=r["scope"], key=r["key"],
                value=r["value"], category=r["category"],
                source=r["source"], created_at=r["created_at"],
                updated_at=r["updated_at"],
                access_count=r["access_count"],
                importance=r["importance"],
            )
            for r in rows
        ]

    def update_importance(self, entry_id: int, importance: float) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE long_term_memory SET importance = ?, updated_at = ? WHERE id = ?",
            (importance, time.time(), entry_id),
        )
        conn.commit()

    def delete(self, entry_id: int) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM long_term_memory WHERE id = ?", (entry_id,))
        conn.execute("DELETE FROM long_term_memory_fts WHERE rowid = ?", (entry_id,))
        conn.commit()

    def compress(self, keep_top: int = 500) -> int:
        """Remove low-importance entries beyond keep_top."""
        conn = self._get_conn()
        count = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
        if count <= keep_top:
            return 0
        excess = count - keep_top
        conn.execute(
            """DELETE FROM long_term_memory WHERE id IN (
                SELECT id FROM long_term_memory
                ORDER BY importance DESC, access_count DESC
                LIMIT -1 OFFSET ?
            )""",
            (keep_top,),
        )
        conn.execute(
            "DELETE FROM long_term_memory_fts WHERE rowid NOT IN (SELECT id FROM long_term_memory)"
        )
        conn.commit()
        return excess

    def to_prompt_block(self, max_chars: int = 2000, query: str = "") -> str:
        entries = self.get_recent(limit=10)
        if not entries:
            return ""
        parts = ["PERSISTENT MEMORY:"]
        for entry in entries:
            category_tag = f"[{entry.category}]"
            value_short = entry.value[:120]
            parts.append(f"  {category_tag} {value_short}")
        block = "\n".join(parts)
        if len(block) > max_chars:
            return block[:max_chars] + "\n... (truncated)"
        return block

    def get_stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
        by_scope = conn.execute(
            "SELECT scope, COUNT(*) as cnt FROM long_term_memory GROUP BY scope"
        ).fetchall()
        return {
            "total_entries": total,
            "by_scope": {r["scope"]: r["cnt"] for r in by_scope},
        }

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
