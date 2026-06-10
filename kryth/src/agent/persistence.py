"""Cross-session persistence for KRYTH AI Coder.

Each REPL run writes its message history to a per-session JSONL file
under ``~/.kryth/sessions/<project-hash>/<session-id>.jsonl``.
Sessions are scoped by project so the same agent in two different
checkouts doesn't conflate history.

Format
------

Each session file is append-only JSONL:

    {"_meta": {...}}                 # first line: session metadata
    {"role": "user", "content": ...} # one OpenAI-shaped message per line
    {"role": "assistant", ...}
    ...

The metadata line carries the project hash, start time, cwd, and any
session-level state we want to recover (mode, cumulative tokens).
Append-only means a crash mid-write loses at most one message, never
the whole transcript.

Public surface
--------------

    SessionStore                              # singleton-ish facade
    session_store()                           # accessor
    list_recent(project_hash=None, limit=20)  # for /resume
    load_session(session_id, project_hash)    # rehydrate
    project_hash_for(cwd='.')                 # stable project identity

Hooked into ``Session.append`` at agent_loop turn boundaries — every
mutation persists. Retention: keep the 20 most recent sessions per
project, prune older ones lazily on session start.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from agent.env import getenv_bool, home_dir


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _root_dir() -> Path:
    return home_dir()


def _sessions_root() -> Path:
    return _root_dir() / "sessions"


def project_hash_for(cwd: str | Path = ".") -> str:
    """Stable 12-char hash of the absolute project path. Used as a
    directory under ~/.kryth/sessions/ so each project's history is
    isolated."""
    abs_path = str(Path(cwd).resolve())
    digest = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()
    return digest[:12]


def _session_dir(project_hash: str) -> Path:
    d = _sessions_root() / project_hash
    return d


def _ensure_dir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Session metadata
# ---------------------------------------------------------------------------

@dataclass
class SessionMeta:
    """First-line metadata of a persisted session file."""
    session_id: str
    project_hash: str
    project_path: str
    started_at: float       # unix seconds
    last_updated: float
    cumulative_in_tokens: int = 0
    cumulative_out_tokens: int = 0
    mode: str = "default"
    profile: str = "default"
    message_count: int = 0
    # Cached first user message preview so /resume can show it without
    # reading the whole transcript.
    first_user_preview: str = ""


def _meta_to_dict(m: SessionMeta) -> dict:
    return {
        "_meta": {
            "session_id": m.session_id,
            "project_hash": m.project_hash,
            "project_path": m.project_path,
            "started_at": m.started_at,
            "last_updated": m.last_updated,
            "cumulative_in_tokens": m.cumulative_in_tokens,
            "cumulative_out_tokens": m.cumulative_out_tokens,
            "mode": m.mode,
            "profile": m.profile,
            "message_count": m.message_count,
            "first_user_preview": m.first_user_preview,
        }
    }


def _meta_from_dict(d: dict) -> SessionMeta | None:
    body = d.get("_meta")
    if not isinstance(body, dict):
        return None
    return SessionMeta(
        session_id=body.get("session_id", ""),
        project_hash=body.get("project_hash", ""),
        project_path=body.get("project_path", ""),
        started_at=float(body.get("started_at", 0.0)),
        last_updated=float(body.get("last_updated", 0.0)),
        cumulative_in_tokens=int(body.get("cumulative_in_tokens", 0)),
        cumulative_out_tokens=int(body.get("cumulative_out_tokens", 0)),
        mode=str(body.get("mode", "default")),
        profile=str(body.get("profile", "default")),
        message_count=int(body.get("message_count", 0)),
        first_user_preview=str(body.get("first_user_preview", "")),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

# Bounded retention per project — older sessions get pruned.
MAX_SESSIONS_PER_PROJECT = 20

# Skip persistence entirely when this env is set. Useful for tests, for
# one-off scripts that shouldn't pollute the user's session history,
# and for users who don't want anything written to disk.
DISABLE_ENV = "KRYTH_NO_PERSIST"


def _persistence_enabled() -> bool:
    return not getenv_bool(DISABLE_ENV)


class SessionStore:
    """One per REPL run. Owns the active session file and provides
    read access to historical sessions for ``/resume``.

    Public methods:

        start_new(cwd)          create a fresh persisted session
        attach(session_id)      open an existing session for append
        append_message(msg)     persist one OpenAI-shaped message
        update_meta(**fields)   refresh metadata (tokens, mode, count)
        flush()                 force fsync (called on shutdown / ^C)
        close()                 release file handle
        active_meta()           current meta (or None when disabled)
    """

    def __init__(self) -> None:
        self._meta: SessionMeta | None = None
        self._file = None  # type: ignore[assignment]
        self._enabled = _persistence_enabled()

    # -- lifecycle ----------------------------------------------------

    def start_new(self, cwd: str | Path = ".") -> SessionMeta | None:
        if not self._enabled:
            return None
        self.close()
        ph = project_hash_for(cwd)
        sd = _session_dir(ph)
        _ensure_dir(sd)
        _prune_old_sessions(sd)

        sid = uuid.uuid4().hex[:12]
        now = time.time()
        meta = SessionMeta(
            session_id=sid,
            project_hash=ph,
            project_path=str(Path(cwd).resolve()),
            started_at=now,
            last_updated=now,
        )
        try:
            self._file = open(sd / f"{sid}.jsonl", "a", encoding="utf-8")
            self._meta = meta
            self._write_line(_meta_to_dict(meta))
            _write_latest_pointer(sd, sid)
        except OSError:
            self._enabled = False
            self._file = None
            self._meta = None
        return self._meta

    def attach(self, session_id: str, project_hash: str | None = None) -> SessionMeta | None:
        """Open an existing session file for append. Used by ``/resume``."""
        if not self._enabled:
            return None
        self.close()
        ph = project_hash or project_hash_for(".")
        sd = _session_dir(ph)
        path = sd / f"{session_id}.jsonl"
        if not path.is_file():
            return None
        meta = _load_meta(path)
        if meta is None:
            return None
        try:
            self._file = open(path, "a", encoding="utf-8")
            self._meta = meta
            _write_latest_pointer(sd, session_id)
        except OSError:
            self._enabled = False
            self._file = None
            self._meta = None
        return self._meta

    def close(self) -> None:
        f = self._file
        self._file = None
        self._meta = None
        if f is not None:
            try:
                f.flush()
                f.close()
            except OSError:
                pass

    def flush(self) -> None:
        f = self._file
        if f is None:
            return
        try:
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        except OSError:
            pass

    # -- writers ------------------------------------------------------

    def append_message(self, msg: dict) -> None:
        if self._file is None or self._meta is None:
            return
        # Skip persistence of obvious non-messages so the file stays tidy.
        if not isinstance(msg, dict) or "role" not in msg:
            return

        self._write_line(msg)

        self._meta.last_updated = time.time()
        self._meta.message_count += 1

        # Capture the first user message for the resume preview.
        if (
            not self._meta.first_user_preview
            and msg.get("role") == "user"
            and isinstance(msg.get("content"), str)
        ):
            preview = msg["content"].strip().splitlines()[0] if msg["content"].strip() else ""
            self._meta.first_user_preview = preview[:120]

    def update_meta(
        self,
        *,
        cumulative_in_tokens: int | None = None,
        cumulative_out_tokens: int | None = None,
        mode: str | None = None,
        profile: str | None = None,
    ) -> None:
        """Refresh in-memory metadata. Persisted on next ``write_meta_marker``
        or implicit shutdown — we don't rewrite the meta line on every
        token update (that would make the file non-append-only)."""
        if self._meta is None:
            return
        if cumulative_in_tokens is not None:
            self._meta.cumulative_in_tokens = cumulative_in_tokens
        if cumulative_out_tokens is not None:
            self._meta.cumulative_out_tokens = cumulative_out_tokens
        if mode is not None:
            self._meta.mode = mode
        if profile is not None:
            self._meta.profile = profile

    def write_meta_marker(self) -> None:
        """Append a fresh ``_meta`` line with current cumulative state.
        Useful at turn boundaries so ``/resume`` sees up-to-date token
        counts without rewriting the whole file."""
        if self._file is None or self._meta is None:
            return
        self._meta.last_updated = time.time()
        self._write_line(_meta_to_dict(self._meta))

    def append_checkpoint(
        self,
        label: str,
        summary: str = "",
        modified_files=None,
    ) -> dict | None:
        """Record a milestone marker in the session transcript.

        Checkpoints are first-class JSONL entries under the
        ``_checkpoint`` key. Unlike messages, they're not replayed to the
        LLM on ``/resume`` — they exist so the operator (and tools like
        ``list_checkpoints``) can see "agent finished phase X here"
        without diffing the whole transcript.

        Returns the checkpoint dict on success, ``None`` when persistence
        is disabled or unavailable.
        """
        if self._file is None or self._meta is None:
            return None
        files = list(modified_files or [])[:32]
        cp = {
            "label": str(label)[:120],
            "summary": str(summary)[:1000],
            "ts": time.time(),
            "turn_index": self._meta.message_count,
            "modified_files": files,
        }
        self._write_line({"_checkpoint": cp})
        self._meta.last_updated = cp["ts"]
        return cp

    # -- internals ----------------------------------------------------

    def _write_line(self, obj: dict) -> None:
        if self._file is None:
            return
        try:
            self._file.write(json.dumps(obj, ensure_ascii=False))
            self._file.write("\n")
            self._file.flush()
        except (OSError, TypeError):
            # Swallow persistence errors — losing a session record is
            # worse UX than crashing the agent over it.
            pass

    def active_meta(self) -> SessionMeta | None:
        return self._meta


# Module-level instance — one persistence store per REPL process.
_store = SessionStore()


def session_store() -> SessionStore:
    return _store


# ---------------------------------------------------------------------------
# Reading historical sessions (for /resume)
# ---------------------------------------------------------------------------

def list_recent(
    project_hash: str | None = None,
    *,
    limit: int = MAX_SESSIONS_PER_PROJECT,
) -> list[SessionMeta]:
    """Return the most recent persisted sessions for the given project,
    newest first. If ``project_hash`` is omitted, uses the current cwd."""
    ph = project_hash or project_hash_for(".")
    sd = _session_dir(ph)
    if not sd.is_dir():
        return []

    metas: list[SessionMeta] = []
    for path in sd.iterdir():
        if path.suffix != ".jsonl":
            continue
        meta = _load_meta(path)
        if meta is not None:
            metas.append(meta)

    metas.sort(key=lambda m: m.last_updated, reverse=True)
    return metas[:limit]


def load_session(
    session_id: str,
    project_hash: str | None = None,
) -> tuple[SessionMeta, list[dict]] | None:
    """Return ``(meta, messages)`` for the named session, or ``None``
    when not found. ``messages`` excludes ``_meta`` and ``_checkpoint``
    markers — those are session-level metadata, not conversation."""
    ph = project_hash or project_hash_for(".")
    sd = _session_dir(ph)
    path = sd / f"{session_id}.jsonl"
    if not path.is_file():
        return None

    meta: SessionMeta | None = None
    messages: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "_meta" in obj:
                    meta = _meta_from_dict(obj) or meta
                elif "_checkpoint" in obj:
                    continue
                else:
                    messages.append(obj)
    except OSError:
        return None

    if meta is None:
        return None
    return meta, messages


def list_checkpoints(
    session_id: str,
    project_hash: str | None = None,
) -> list[dict]:
    """Return all milestone checkpoints written into the session, newest
    first. Each entry: ``{label, summary, ts, turn_index, modified_files}``.
    """
    ph = project_hash or project_hash_for(".")
    sd = _session_dir(ph)
    path = sd / f"{session_id}.jsonl"
    if not path.is_file():
        return []

    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or "_checkpoint" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cp = obj.get("_checkpoint") if isinstance(obj, dict) else None
                if isinstance(cp, dict):
                    out.append(cp)
    except OSError:
        return []

    out.sort(key=lambda c: c.get("ts", 0.0), reverse=True)
    return out


def latest_session_id(project_hash: str | None = None) -> str | None:
    """Quick read of the ``latest.json`` pointer; falls back to scanning
    the directory if the pointer is missing or stale."""
    ph = project_hash or project_hash_for(".")
    sd = _session_dir(ph)
    latest_file = sd / "latest.json"
    if latest_file.is_file():
        try:
            data = json.loads(latest_file.read_text(encoding="utf-8"))
            sid = data.get("session_id")
            if isinstance(sid, str) and (sd / f"{sid}.jsonl").is_file():
                return sid
        except (OSError, json.JSONDecodeError):
            pass
    metas = list_recent(ph, limit=1)
    return metas[0].session_id if metas else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_meta(path: Path) -> SessionMeta | None:
    """Stream the file looking for the most recent ``_meta`` line. We
    write a fresh marker at turn boundaries, so the LAST meta carries
    the up-to-date counts; the first carries the original start time
    (we preserve it explicitly when updating)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None

    meta: SessionMeta | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = _meta_from_dict(obj)
        if m is not None:
            if meta is None:
                meta = m
            else:
                # Preserve original start time, update everything else.
                started = meta.started_at
                meta = m
                meta.started_at = started
    return meta


def _write_latest_pointer(session_dir: Path, session_id: str) -> None:
    try:
        (session_dir / "latest.json").write_text(
            json.dumps({"session_id": session_id, "ts": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        pass


def _prune_old_sessions(session_dir: Path) -> None:
    """Keep ``MAX_SESSIONS_PER_PROJECT`` files; delete older ones. Runs
    lazily on session start so no background task is needed."""
    try:
        files = sorted(
            (p for p in session_dir.iterdir() if p.suffix == ".jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in files[MAX_SESSIONS_PER_PROJECT:]:
        try:
            old.unlink()
        except OSError:
            pass


__all__ = [
    "SessionMeta",
    "SessionStore",
    "session_store",
    "project_hash_for",
    "list_recent",
    "load_session",
    "list_checkpoints",
    "latest_session_id",
    "MAX_SESSIONS_PER_PROJECT",
    "DISABLE_ENV",
]
