"""Login memory — detect, store, and reuse authenticated sessions.

Stores login records in a SQLite database so sessions survive across
Python process restarts.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from agent.browser.config import BROWSER_PROFILES_DB, SENSITIVE_DOMAINS, SESSION_CACHE_TTL

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS login_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    site        TEXT    NOT NULL,
    profile     TEXT    NOT NULL,
    project     TEXT    NOT NULL DEFAULT '',
    login_time  REAL    NOT NULL,
    last_seen   REAL    NOT NULL,
    valid       INTEGER NOT NULL DEFAULT 1,
    approved    INTEGER NOT NULL DEFAULT 0,
    notes       TEXT    NOT NULL DEFAULT '',
    UNIQUE(site, profile, project)
);
"""


class LoginMemory:
    """Persist knowledge about which sites are currently logged-in per profile.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Shared across all profiles.
    """

    def __init__(self, db_path: Path = BROWSER_PROFILES_DB) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Session recording
    # ------------------------------------------------------------------

    def record_login(
        self,
        url: str,
        profile: str,
        project: str = "",
        approved: bool = False,
        notes: str = "",
    ) -> None:
        """Record a successful login for *url* under *profile*."""
        site = _extract_site(url)
        now = time.time()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO login_sessions(site, profile, project, login_time, last_seen, valid, approved, notes)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(site, profile, project) DO UPDATE SET
                    last_seen  = excluded.last_seen,
                    valid      = 1,
                    approved   = MAX(approved, excluded.approved),
                    notes      = excluded.notes
                """,
                (site, profile, project, now, now, int(approved), notes),
            )

    def mark_invalid(self, url: str, profile: str, project: str = "") -> None:
        """Mark the session for *url* as no longer valid."""
        site = _extract_site(url)
        with self._connect() as con:
            con.execute(
                "UPDATE login_sessions SET valid=0 WHERE site=? AND profile=? AND project=?",
                (site, profile, project),
            )

    def touch(self, url: str, profile: str, project: str = "") -> None:
        """Update the last_seen timestamp for an existing session."""
        site = _extract_site(url)
        with self._connect() as con:
            con.execute(
                "UPDATE login_sessions SET last_seen=? WHERE site=? AND profile=? AND project=? AND valid=1",
                (time.time(), site, profile, project),
            )

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def is_logged_in(self, url: str, profile: str, project: str = "") -> bool:
        """Return True if a valid (and approved) session is remembered."""
        site = _extract_site(url)
        with self._connect() as con:
            row = con.execute(
                "SELECT last_seen FROM login_sessions WHERE site=? AND profile=? AND project=? AND valid=1 AND approved=1",
                (site, profile, project),
            ).fetchone()
        if row is None:
            return False
        age = time.time() - row[0]
        return age < SESSION_CACHE_TTL * 288  # ~24 h stale threshold

    def get_all_sessions(
        self, profile: str = "", project: str = "", valid_only: bool = True
    ) -> list[dict]:
        """Return all known login sessions, optionally filtered."""
        query = "SELECT site, profile, project, login_time, last_seen, valid, approved, notes FROM login_sessions WHERE 1=1"
        params: list = []
        if profile:
            query += " AND profile=?"
            params.append(profile)
        if project:
            query += " AND project=?"
            params.append(project)
        if valid_only:
            query += " AND valid=1"
        query += " ORDER BY last_seen DESC"
        with self._connect() as con:
            rows = con.execute(query, params).fetchall()
        return [
            dict(zip(["site", "profile", "project", "login_time", "last_seen", "valid", "approved", "notes"], r))
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Approval
    # ------------------------------------------------------------------

    def needs_approval(self, url: str) -> bool:
        """Return True if *url* is a sensitive site requiring human approval."""
        site = _extract_site(url)
        return any(site == d or site.endswith("." + d) for d in SENSITIVE_DOMAINS)

    def approve_session(self, url: str, profile: str, project: str = "") -> None:
        site = _extract_site(url)
        with self._connect() as con:
            con.execute(
                "UPDATE login_sessions SET approved=1 WHERE site=? AND profile=? AND project=?",
                (site, profile, project),
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))


def _extract_site(url: str) -> str:
    """Return the netloc of *url*, or the raw string if parsing fails."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        return parsed.netloc or url
    except Exception:
        return url
