"""Experience Store — single SQLite database that records every experience.

Never duplicates what existing memory systems already store. This store
holds only the *ranked composite experience record* that cross-references
IDs from ExecutionMemory, WorkflowMemory, FailureMemory, DecisionMemory,
TeamMemory. Each row is a lightweight pointer + score, not a copy.

Database: ~/.kryth/experience/<project_hash>.db
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _home() -> Path:
    try:
        from agent.env import home_dir
        return home_dir()
    except Exception:
        return Path.home() / ".kryth"


_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS experiences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_hash    TEXT NOT NULL,
    kind            TEXT NOT NULL,
    -- kinds: task, team, workflow, repair, terminal, decision, architecture, parallel
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL,
    tags_json       TEXT DEFAULT '[]',
    source_refs_json TEXT DEFAULT '{}',
    -- refs to existing memory DBs: {"execution_id": 3, "workflow_id": 7, ...}
    outcome         TEXT NOT NULL DEFAULT 'success',
    -- 'success', 'failure', 'partial'
    importance      REAL DEFAULT 0.5,
    confidence      REAL DEFAULT 0.5,
    success_rate    REAL DEFAULT 1.0,
    reuse_count     INTEGER DEFAULT 0,
    novelty         REAL DEFAULT 1.0,
    recency_ts      REAL NOT NULL,
    created_at      REAL NOT NULL,
    composite_score REAL DEFAULT 0.5,
    extra_json      TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_exp_kind       ON experiences(kind);
CREATE INDEX IF NOT EXISTS idx_exp_score      ON experiences(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_exp_project    ON experiences(project_hash);
CREATE INDEX IF NOT EXISTS idx_exp_recency    ON experiences(recency_ts DESC);
CREATE INDEX IF NOT EXISTS idx_exp_outcome    ON experiences(outcome);

CREATE VIRTUAL TABLE IF NOT EXISTS experiences_fts USING fts5(
    exp_id UNINDEXED,
    title,
    summary,
    tags_json,
    tokenize='unicode61 remove_diacritics 1'
);
"""


@dataclass
class ExperienceRecord:
    id: Optional[int]
    project_hash: str
    kind: str
    title: str
    summary: str
    tags: List[str]
    source_refs: Dict[str, Any]    # pointers into existing memory DBs
    outcome: str                   # success | failure | partial
    importance: float
    confidence: float
    success_rate: float
    reuse_count: int
    novelty: float
    recency_ts: float
    created_at: float
    composite_score: float
    extra: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "tags": self.tags,
            "source_refs": self.source_refs,
            "outcome": self.outcome,
            "importance": self.importance,
            "confidence": self.confidence,
            "success_rate": self.success_rate,
            "reuse_count": self.reuse_count,
            "novelty": self.novelty,
            "composite_score": self.composite_score,
            "recency_ts": self.recency_ts,
            "extra": self.extra,
        }


class ExperienceStore:
    """Thin SQLite layer — write/read experience records and FTS search."""

    def __init__(self, project_hash: str) -> None:
        self._ph = project_hash
        db_dir = _home() / "experience"
        db_dir.mkdir(parents=True, exist_ok=True)
        self._path = str(db_dir / f"{project_hash}.db")
        self._conn: Optional[sqlite3.Connection] = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        return self._conn

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def insert(self, rec: ExperienceRecord) -> int:
        db = self._db()
        score = _compute_score(rec)
        cur = db.execute(
            """
            INSERT INTO experiences
                (project_hash, kind, title, summary, tags_json, source_refs_json,
                 outcome, importance, confidence, success_rate, reuse_count,
                 novelty, recency_ts, created_at, composite_score, extra_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rec.project_hash, rec.kind, rec.title, rec.summary,
                json.dumps(rec.tags), json.dumps(rec.source_refs),
                rec.outcome, rec.importance, rec.confidence,
                rec.success_rate, rec.reuse_count, rec.novelty,
                rec.recency_ts, rec.created_at, score,
                json.dumps(rec.extra),
            ),
        )
        new_id = cur.lastrowid
        # Write to standalone FTS table immediately (no trigger dependency)
        db.execute(
            "INSERT INTO experiences_fts(exp_id, title, summary, tags_json) VALUES (?,?,?,?)",
            (new_id, rec.title, rec.summary, json.dumps(rec.tags)),
        )
        db.commit()
        return new_id  # type: ignore[return-value]

    def update_reuse(self, exp_id: int) -> None:
        db = self._db()
        now = time.time()
        db.execute(
            "UPDATE experiences SET reuse_count=reuse_count+1, recency_ts=? WHERE id=?",
            (now, exp_id),
        )
        db.commit()

    def update_outcome(self, exp_id: int, outcome: str, new_success_rate: float) -> None:
        db = self._db()
        db.execute(
            "UPDATE experiences SET outcome=?, success_rate=? WHERE id=?",
            (outcome, new_success_rate, exp_id),
        )
        db.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def search_fts(self, query: str, kind: Optional[str] = None, limit: int = 10) -> List[ExperienceRecord]:
        """FTS5 search over title + summary + tags."""
        db = self._db()
        # Sanitise: strip FTS special chars that break the parser
        safe = " ".join(
            w.strip("\"'()") for w in query.split() if w.strip("\"'()")
        )
        if not safe:
            return []
        fts_q = " ".join(f"{w}*" for w in safe.split())
        try:
            if kind:
                rows = db.execute(
                    """
                    SELECT e.* FROM experiences e
                    JOIN experiences_fts f ON e.id = CAST(f.exp_id AS INTEGER)
                    WHERE f.experiences_fts MATCH ?
                      AND e.kind = ?
                      AND e.project_hash = ?
                    ORDER BY e.composite_score DESC
                    LIMIT ?
                    """,
                    (fts_q, kind, self._ph, limit),
                ).fetchall()
            else:
                rows = db.execute(
                    """
                    SELECT e.* FROM experiences e
                    JOIN experiences_fts f ON e.id = CAST(f.exp_id AS INTEGER)
                    WHERE f.experiences_fts MATCH ?
                      AND e.project_hash = ?
                    ORDER BY e.composite_score DESC
                    LIMIT ?
                    """,
                    (fts_q, self._ph, limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [_row_to_record(r) for r in rows]

    def get_by_kind(self, kind: str, limit: int = 20, min_score: float = 0.0) -> List[ExperienceRecord]:
        rows = self._db().execute(
            """SELECT * FROM experiences
               WHERE kind=? AND project_hash=? AND composite_score>=?
               ORDER BY composite_score DESC LIMIT ?""",
            (kind, self._ph, min_score, limit),
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def get_recent(self, limit: int = 20) -> List[ExperienceRecord]:
        rows = self._db().execute(
            "SELECT * FROM experiences WHERE project_hash=? ORDER BY recency_ts DESC LIMIT ?",
            (self._ph, limit),
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def get_by_id(self, exp_id: int) -> Optional[ExperienceRecord]:
        row = self._db().execute(
            "SELECT * FROM experiences WHERE id=?", (exp_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def stats(self) -> Dict[str, Any]:
        db = self._db()
        total = db.execute(
            "SELECT COUNT(*) FROM experiences WHERE project_hash=?", (self._ph,)
        ).fetchone()[0]
        by_kind = {
            row[0]: row[1]
            for row in db.execute(
                "SELECT kind, COUNT(*) FROM experiences WHERE project_hash=? GROUP BY kind",
                (self._ph,),
            ).fetchall()
        }
        avg_score = (
            db.execute(
                "SELECT AVG(composite_score) FROM experiences WHERE project_hash=?",
                (self._ph,),
            ).fetchone()[0]
            or 0.0
        )
        return {
            "total": total,
            "by_kind": by_kind,
            "avg_composite_score": round(avg_score, 3),
            "db_path": self._path,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _compute_score(rec: ExperienceRecord) -> float:
    age_days = (time.time() - rec.created_at) / 86400
    recency = 0.5 ** (age_days / 30)  # half-life 30 days
    usage = min(rec.reuse_count, 100) / 100
    return (
        0.30 * rec.importance
        + 0.25 * rec.confidence
        + 0.20 * rec.success_rate
        + 0.15 * usage
        + 0.05 * recency
        + 0.05 * (1.0 - rec.novelty)  # low novelty = well-proven = higher score
    )


def _row_to_record(row: sqlite3.Row) -> ExperienceRecord:
    return ExperienceRecord(
        id=row["id"],
        project_hash=row["project_hash"],
        kind=row["kind"],
        title=row["title"],
        summary=row["summary"],
        tags=json.loads(row["tags_json"] or "[]"),
        source_refs=json.loads(row["source_refs_json"] or "{}"),
        outcome=row["outcome"],
        importance=row["importance"],
        confidence=row["confidence"],
        success_rate=row["success_rate"],
        reuse_count=row["reuse_count"],
        novelty=row["novelty"],
        recency_ts=row["recency_ts"],
        created_at=row["created_at"],
        composite_score=row["composite_score"],
        extra=json.loads(row["extra_json"] or "{}"),
    )


# ------------------------------------------------------------------
# Project hash helper
# ------------------------------------------------------------------

def project_hash(root: str = ".") -> str:
    return hashlib.md5(
        os.path.abspath(root).encode(), usedforsecurity=False
    ).hexdigest()[:16]


# ------------------------------------------------------------------
# Module-level singletons
# ------------------------------------------------------------------

_stores: Dict[str, ExperienceStore] = {}


def get_store(root: str = ".") -> ExperienceStore:
    ph = project_hash(root)
    if ph not in _stores:
        _stores[ph] = ExperienceStore(ph)
    return _stores[ph]
