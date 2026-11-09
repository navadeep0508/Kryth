from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from config.loader import get_data_dir

STORE_VERSION = 1

META_FILE = "meta.json"
CHECKPOINT_FILE = "checkpoints.jsonl"

TITLE_LENGTH = 72

UNTITLED = "(untitled)"


def get_sessions_dir() -> Path:
    return get_data_dir() / "sessions"


def make_title(message: str) -> str:
    """A one-line label for a session, taken from the prompt that started it."""

    text = " ".join(message.split())
    if not text:
        return UNTITLED
    if len(text) <= TITLE_LENGTH:
        return text
    return text[: TITLE_LENGTH - 1].rstrip() + "…"


def _parse_time(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


def _write_atomic(path: Path, content: str) -> None:
    # A half-written meta.json would take the whole session down with it.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


@dataclass
class Checkpoint:
    """A saved transcript inside a session."""

    id: str
    label: str
    created_at: datetime
    turn: int
    message_count: int
    auto: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "created_at": self.created_at.isoformat(),
            "turn": self.turn,
            "message_count": self.message_count,
            "auto": self.auto,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        return cls(
            id=str(data.get("id", "")),
            label=str(data.get("label", "")),
            created_at=_parse_time(data.get("created_at")),
            turn=int(data.get("turn", 0) or 0),
            message_count=int(data.get("message_count", 0) or 0),
            auto=bool(data.get("auto", True)),
        )


@dataclass
class SessionMeta:
    """Everything needed to list a session without reading its transcript."""

    session_id: str
    cwd: str
    model: str = ""
    title: str = UNTITLED
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    version: int = STORE_VERSION

    @property
    def short_id(self) -> str:
        return self.session_id[:8]

    @property
    def head(self) -> Checkpoint | None:
        return self.checkpoints[-1] if self.checkpoints else None

    @property
    def message_count(self) -> int:
        head = self.head
        return head.message_count if head else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "model": self.model,
            "title": self.title,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "turns": self.turns,
            "usage": self.usage,
            "checkpoints": [checkpoint.to_dict() for checkpoint in self.checkpoints],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMeta:
        return cls(
            session_id=str(data.get("session_id", "")),
            cwd=str(data.get("cwd", "")),
            model=str(data.get("model", "")),
            title=str(data.get("title") or UNTITLED),
            created_at=_parse_time(data.get("created_at")),
            updated_at=_parse_time(data.get("updated_at")),
            turns=int(data.get("turns", 0) or 0),
            usage={k: int(v) for k, v in (data.get("usage") or {}).items()},
            checkpoints=[
                Checkpoint.from_dict(item) for item in data.get("checkpoints") or []
            ],
            version=int(data.get("version", STORE_VERSION) or STORE_VERSION),
        )


@dataclass
class SessionRecord:
    """A session plus the transcript of one of its checkpoints."""

    meta: SessionMeta
    checkpoint: Checkpoint
    messages: list[dict[str, Any]]


def _keep_checkpoints(checkpoints: list[Checkpoint], limit: int) -> list[Checkpoint]:
    """Trim to `limit`, dropping autosaves before anything the user named."""

    kept = list(checkpoints)
    if len(kept) <= limit:
        return kept

    for auto_first in (True, False):
        for checkpoint in list(kept):
            if len(kept) <= limit:
                return kept
            # The newest checkpoint is the session head, it always stays.
            if checkpoint is kept[-1]:
                continue
            if checkpoint.auto is auto_first:
                kept.remove(checkpoint)

    return kept


class SessionStore:

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_sessions_dir()

    def _dir(self, session_id: str) -> Path:
        return self.root / session_id

    def exists(self, session_id: str) -> bool:
        return (self._dir(session_id) / META_FILE).is_file()

    def read_meta(self, session_id: str) -> SessionMeta | None:
        path = self._dir(session_id) / META_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return SessionMeta.from_dict(data)

    def list(self, cwd: Path | str | None = None) -> list[SessionMeta]:
        """Saved sessions, newest first, optionally scoped to one project."""

        if not self.root.is_dir():
            return []

        wanted = str(Path(cwd).resolve()) if cwd is not None else None

        sessions: list[SessionMeta] = []
        for entry in self.root.iterdir():
            if not entry.is_dir():
                continue
            meta = self.read_meta(entry.name)
            if meta is None or not meta.session_id:
                continue
            if wanted is not None and meta.cwd != wanted:
                continue
            sessions.append(meta)

        sessions.sort(key=lambda meta: meta.updated_at, reverse=True)
        return sessions

    def latest(self, cwd: Path | str | None = None) -> SessionMeta | None:
        sessions = self.list(cwd)
        return sessions[0] if sessions else None

    def resolve(self, session_id: str, cwd: Path | str | None = None) -> str | None:
        """Accept a full id or any unique-enough prefix, newest match wins."""

        if not session_id:
            return None
        if self.exists(session_id):
            return session_id

        matches = [
            meta.session_id
            for meta in self.list()
            if meta.session_id.startswith(session_id)
        ]
        if not matches:
            return None
        return matches[0]

    def save(
        self,
        meta: SessionMeta,
        messages: list[dict[str, Any]],
        label: str | None = None,
        auto: bool = True,
        max_checkpoints: int = 20,
    ) -> Checkpoint:
        """Append a checkpoint and refresh the session metadata."""

        directory = self._dir(meta.session_id)
        directory.mkdir(parents=True, exist_ok=True)

        stored = self.read_meta(meta.session_id)
        checkpoints = list(stored.checkpoints) if stored else []
        created_at = stored.created_at if stored else meta.created_at

        checkpoint = Checkpoint(
            id=uuid.uuid4().hex[:8],
            label=label or f"turn {meta.turns}",
            created_at=datetime.now(),
            turn=meta.turns,
            message_count=len(messages),
            auto=auto,
        )
        checkpoints.append(checkpoint)

        record = {
            "id": checkpoint.id,
            "created_at": checkpoint.created_at.isoformat(),
            "turn": checkpoint.turn,
            "messages": messages,
        }
        with (directory / CHECKPOINT_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

        kept = _keep_checkpoints(checkpoints, max_checkpoints)
        if len(kept) != len(checkpoints):
            self._rewrite_checkpoints(meta.session_id, {item.id for item in kept})

        meta.created_at = created_at
        meta.updated_at = checkpoint.created_at
        meta.checkpoints = kept
        meta.version = STORE_VERSION

        _write_atomic(
            directory / META_FILE,
            json.dumps(meta.to_dict(), indent=2, ensure_ascii=False),
        )

        return checkpoint

    def load(self, session_id: str, checkpoint_id: str | None = None) -> SessionRecord | None:
        """The transcript of one checkpoint, or of the session head."""

        meta = self.read_meta(session_id)
        if meta is None:
            return None

        target: Checkpoint | None
        if checkpoint_id is None:
            target = meta.head
        else:
            target = next(
                (item for item in meta.checkpoints if item.id.startswith(checkpoint_id)),
                None,
            )
        if target is None:
            return None

        for record in self._iter_records(session_id):
            if record.get("id") != target.id:
                continue
            messages = record.get("messages")
            if not isinstance(messages, list):
                return None
            return SessionRecord(meta=meta, checkpoint=target, messages=messages)

        return None

    def delete(self, session_id: str) -> bool:
        directory = self._dir(session_id)
        if not directory.is_dir():
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return not directory.exists()

    def prune(self, keep: int) -> int:
        """Drop the oldest sessions once there are more than `keep` of them."""

        sessions = self.list()
        removed = 0
        for meta in sessions[keep:]:
            if self.delete(meta.session_id):
                removed += 1
        return removed

    def _iter_records(self, session_id: str) -> Iterator[dict[str, Any]]:
        path = self._dir(session_id) / CHECKPOINT_FILE
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError:
            return
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record

    def _rewrite_checkpoints(self, session_id: str, keep: set[str]) -> None:
        records = [
            record for record in self._iter_records(session_id) if record.get("id") in keep
        ]
        lines = "".join(
            json.dumps(record, ensure_ascii=False) + "\n" for record in records
        )
        _write_atomic(self._dir(session_id) / CHECKPOINT_FILE, lines)
