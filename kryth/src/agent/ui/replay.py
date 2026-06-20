"""Mission replay (Phase 11) — record the UI event stream, replay it later.

Two halves, both pure presentation:

* ``MissionRecorder`` subscribes to the event ``BUS`` and appends every event as
  one JSON line to ``<dir>/<mission_id>.jsonl``. It captures the *display* event
  stream the renderer already sees — it never touches scheduler/agent state.
* ``replay_mission`` reads a recording back and re-emits the events through the
  live bus (or a provided sink) so the dashboard re-draws the run like a
  recording. Timing between events is preserved (scaled), capped so a long
  mission replays quickly.

Recording is flag-gated (``KRYTH_MISSION_RECORD=1``, default OFF) so normal runs
write nothing. Storage lives under ``~/.kryth/missions`` by default.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from agent.ui.events import BUS, Event, EventKind, emit


def recordings_dir() -> Path:
    """Where mission recordings live. Override with ``KRYTH_MISSION_DIR``."""
    base = os.environ.get("KRYTH_MISSION_DIR")
    if base:
        p = Path(base)
    else:
        p = Path(os.path.expanduser("~")) / ".kryth" / "missions"
    return p


def record_enabled() -> bool:
    return os.environ.get("KRYTH_MISSION_RECORD", "").strip().lower() in ("1", "true", "yes", "on")


def _jsonable(v: Any) -> Any:
    if is_dataclass(v) and not isinstance(v, type):
        return _jsonable(asdict(v))
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return repr(v)


class MissionRecorder:
    """Append-only JSONL recorder bound to the event bus.

    Use as a context manager or call ``start()``/``stop()``. The file is opened
    lazily on the first event and flushed per-line so a crash still leaves a
    replayable prefix. Never raises into the bus — a recording failure must not
    break the run.
    """

    def __init__(self, mission_id: str, *, directory: Optional[Path] = None, bus=BUS):
        self.mission_id = mission_id
        self.dir = Path(directory) if directory else recordings_dir()
        self.path = self.dir / f"{mission_id}.jsonl"
        self._bus = bus
        self._unsub: Optional[Callable[[], None]] = None
        self._fh = None
        self._count = 0
        self._t0: Optional[float] = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> "MissionRecorder":
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "w", encoding="utf-8")
            self._write_header()
        except Exception:
            self._fh = None  # degrade silently; recording is best-effort
        self._unsub = self._bus.subscribe(self._on_event)
        return self

    def stop(self) -> Path:
        if self._unsub:
            try:
                self._unsub()
            except Exception:
                pass
            self._unsub = None
        if self._fh:
            try:
                self._fh.flush(); self._fh.close()
            except Exception:
                pass
            self._fh = None
        return self.path

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False

    # -- recording ----------------------------------------------------------

    def _write_header(self) -> None:
        rec = {"_meta": True, "mission_id": self.mission_id, "version": 1}
        self._fh.write(json.dumps(rec) + "\n")

    def _on_event(self, evt: Event) -> None:
        if not self._fh:
            return
        try:
            if self._t0 is None:
                self._t0 = evt.ts
            row = {
                "kind": evt.kind.value if isinstance(evt.kind, EventKind) else str(evt.kind),
                "t": round(max(0.0, evt.ts - self._t0), 4),
                "data": _jsonable(evt.data),
            }
            self._fh.write(json.dumps(row) + "\n")
            self._fh.flush()
            self._count += 1
        except Exception:
            pass  # one bad event must never break the bus

    @property
    def count(self) -> int:
        return self._count


# ── Playback ────────────────────────────────────────────────────────────────

def load_recording(path) -> list:
    """Read a recording into a list of ``{kind, t, data}`` rows (skips header)."""
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("_meta"):
            continue
        rows.append(obj)
    return rows


def list_missions(directory: Optional[Path] = None) -> list:
    """Mission ids with recordings on disk, newest first."""
    d = Path(directory) if directory else recordings_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [f.stem for f in files]


def replay_mission(
    mission_id: str,
    *,
    directory: Optional[Path] = None,
    sink: Optional[Callable[[str, dict], None]] = None,
    speed: float = 4.0,
    max_gap: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Replay a recording. Re-emits each event through ``sink`` (default: the
    live bus), preserving inter-event timing scaled by ``speed`` and capped at
    ``max_gap`` seconds. Returns the number of events replayed.

    ``sink(kind_str, data)`` lets tests capture without touching the bus.
    ``sleep`` is injectable so tests run instantly.
    """
    d = Path(directory) if directory else recordings_dir()
    rows = load_recording(d / f"{mission_id}.jsonl")
    if not rows:
        return 0

    def _default_sink(kind_str: str, data: dict) -> None:
        try:
            emit(EventKind(kind_str), **data)
        except Exception:
            pass  # unknown/renamed kinds are skipped, not fatal

    out = sink or _default_sink
    prev_t = rows[0].get("t", 0.0)
    n = 0
    for row in rows:
        t = row.get("t", prev_t)
        gap = min(max(0.0, (t - prev_t) / max(speed, 0.01)), max_gap)
        if gap > 0:
            sleep(gap)
        prev_t = t
        out(row.get("kind", ""), row.get("data") or {})
        n += 1
    return n
