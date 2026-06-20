"""SQLite persistence for KRYTH Desktop.

All tables live in ~/.kryth/desktop.db. Thread-safe via threading.Lock.
Uses Python's built-in sqlite3 — no extra dependencies.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path.home() / ".kryth" / "desktop.db"

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    project_path TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    tool_actions TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    path        TEXT UNIQUE NOT NULL,
    last_opened TEXT NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS diffs (
    id             TEXT PRIMARY KEY,
    session_id     TEXT,
    file_path      TEXT NOT NULL,
    before_content TEXT NOT NULL DEFAULT '',
    after_content  TEXT NOT NULL DEFAULT '',
    approved       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id         TEXT PRIMARY KEY,
    session_id TEXT,
    message    TEXT NOT NULL,
    risk       TEXT NOT NULL DEFAULT 'medium',
    approved   INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
    id         TEXT PRIMARY KEY,
    session_id TEXT,
    command    TEXT NOT NULL,
    exit_code  INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session  ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_diffs_session     ON diffs(session_id);
CREATE INDEX IF NOT EXISTS idx_commands_session  ON commands(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_created  ON messages(created_at);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def _exec(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        return _connect().execute(sql, params)


def _exec_many(sql: str, params_seq) -> None:
    with _lock:
        _connect().executemany(sql, params_seq)
        _connect().commit()


def _commit() -> None:
    with _lock:
        _connect().commit()


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def create_session(project_path: str = "", model: str = "") -> str:
    sid = str(uuid.uuid4())
    now = _now()
    _exec(
        "INSERT INTO sessions(id,project_path,model,created_at,updated_at) VALUES(?,?,?,?,?)",
        (sid, project_path, model, now, now),
    )
    _commit()
    return sid


def get_sessions(limit: int = 20) -> list[dict]:
    rows = _exec(
        "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def touch_session(session_id: str) -> None:
    _exec("UPDATE sessions SET updated_at=? WHERE id=?", (_now(), session_id))
    _commit()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def save_message(session_id: str, role: str, content: str,
                 tool_actions: list | None = None) -> str:
    mid = str(uuid.uuid4())
    _exec(
        "INSERT INTO messages(id,session_id,role,content,tool_actions,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (mid, session_id, role, content,
         json.dumps(tool_actions or []), _now()),
    )
    _commit()
    return mid


def get_messages(session_id: str) -> list[dict]:
    rows = _exec(
        "SELECT * FROM messages WHERE session_id=? ORDER BY created_at ASC",
        (session_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["tool_actions"] = json.loads(d.get("tool_actions", "[]"))
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def upsert_project(path: str) -> str:
    name = Path(path).name or path
    now = _now()
    row = _exec("SELECT id FROM projects WHERE path=?", (path,)).fetchone()
    if row:
        pid = row["id"]
        _exec("UPDATE projects SET last_opened=? WHERE id=?", (now, pid))
    else:
        pid = str(uuid.uuid4())
        _exec(
            "INSERT INTO projects(id,name,path,last_opened) VALUES(?,?,?,?)",
            (pid, name, path, now),
        )
    _commit()
    return pid


def get_projects(limit: int = 10) -> list[dict]:
    rows = _exec(
        "SELECT * FROM projects ORDER BY last_opened DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Diffs
# ---------------------------------------------------------------------------

def save_diff(session_id: str, file_path: str,
              before: str, after: str) -> str:
    did = str(uuid.uuid4())
    _exec(
        "INSERT INTO diffs(id,session_id,file_path,before_content,after_content,created_at)"
        " VALUES(?,?,?,?,?,?)",
        (did, session_id, file_path, before, after, _now()),
    )
    _commit()
    return did


def resolve_diff(diff_id: str, approved: bool) -> None:
    _exec("UPDATE diffs SET approved=? WHERE id=?", (int(approved), diff_id))
    _commit()


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

def save_approval(session_id: str, message: str, risk: str) -> str:
    aid = str(uuid.uuid4())
    _exec(
        "INSERT INTO approvals(id,session_id,message,risk,created_at) VALUES(?,?,?,?,?)",
        (aid, session_id, message, risk, _now()),
    )
    _commit()
    return aid


def resolve_approval(approval_id: str, approved: bool) -> None:
    _exec("UPDATE approvals SET approved=? WHERE id=?", (int(approved), approval_id))
    _commit()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    row = _exec("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    _exec(
        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _now()),
    )
    _commit()


def get_all_settings() -> dict[str, str]:
    rows = _exec("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def save_command(session_id: str, command: str, exit_code: int | None = None) -> str:
    cid = str(uuid.uuid4())
    _exec(
        "INSERT INTO commands(id,session_id,command,exit_code,created_at) VALUES(?,?,?,?,?)",
        (cid, session_id, command, exit_code, _now()),
    )
    _commit()
    return cid


def get_command_history(session_id: str, limit: int = 100) -> list[str]:
    rows = _exec(
        "SELECT command FROM commands WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [r["command"] for r in rows]
