"""Persists benchmark runs to JSON and loads history for comparison."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .benchmark_metrics import BenchmarkRun, MissionMetrics


DEFAULT_HISTORY_DIR = str(
    (Path(__file__).parent.parent / "benchmark_history").resolve()
)


def _run_id() -> str:
    return f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


# ── Serialization ─────────────────────────────────────────────────────────────

def save_run(
    run: BenchmarkRun,
    history_dir: str = DEFAULT_HISTORY_DIR,
) -> str:
    """Serialize run to JSON and return the path written."""
    _ensure_dir(history_dir)
    if not run.run_id:
        run.run_id = _run_id()
    if not run.timestamp:
        run.timestamp = datetime.now(timezone.utc).isoformat()
    path = os.path.join(history_dir, f"{run.run_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run.to_dict(), fh, indent=2, default=str)
    return path


def load_run(path: str) -> BenchmarkRun:
    """Load a previously saved run from JSON."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    run = BenchmarkRun(
        run_id=raw.get("run_id", ""),
        timestamp=raw.get("timestamp", ""),
        kryth_version=raw.get("kryth_version", "unknown"),
        timeout_s=raw.get("timeout_s", 300),
    )
    for m_raw in raw.get("missions", []):
        run.missions.append(_load_mission_metrics(m_raw))
    return run


def list_runs(history_dir: str = DEFAULT_HISTORY_DIR) -> list[str]:
    """Return sorted list of run JSON paths (oldest first)."""
    d = Path(history_dir)
    if not d.exists():
        return []
    return sorted(str(p) for p in d.glob("run_*.json"))


def load_latest_run(history_dir: str = DEFAULT_HISTORY_DIR) -> Optional[BenchmarkRun]:
    runs = list_runs(history_dir)
    if not runs:
        return None
    return load_run(runs[-1])


def load_run_by_id(run_id: str, history_dir: str = DEFAULT_HISTORY_DIR) -> Optional[BenchmarkRun]:
    path = os.path.join(history_dir, f"{run_id}.json")
    if not os.path.exists(path):
        return None
    return load_run(path)


# ── Internal deserialization helpers ──────────────────────────────────────────

def _load_mission_metrics(d: dict) -> MissionMetrics:
    from .benchmark_metrics import (
        MissionTimings,
        AgentMetrics,
        StreamingMetrics,
        MemoryMetrics,
        RecoveryMetrics,
        ParallelMetrics,
    )

    def _sub(cls, key: str):
        raw = d.get(key, {})
        obj = cls()
        for field, val in raw.items():
            if hasattr(obj, field):
                setattr(obj, field, val)
        return obj

    m = MissionMetrics(
        mission_id=d.get("mission_id", ""),
        mission_name=d.get("mission_name", ""),
        prompt=d.get("prompt", ""),
        workspace=d.get("workspace", ""),
        success=d.get("success", False),
        error=d.get("error", ""),
        tokens_in=d.get("tokens_in", 0),
        tokens_out=d.get("tokens_out", 0),
        total_tool_calls=d.get("total_tool_calls", 0),
        files_written=d.get("files_written", 0),
        files_read=d.get("files_read", 0),
        commands_run=d.get("commands_run", 0),
        turns_used=d.get("turns_used", 0),
        timings=_sub(MissionTimings, "timings"),
        agents=_sub(AgentMetrics, "agents"),
        streaming=_sub(StreamingMetrics, "streaming"),
        memory=_sub(MemoryMetrics, "memory"),
        recovery=_sub(RecoveryMetrics, "recovery"),
        parallel=_sub(ParallelMetrics, "parallel"),
    )
    return m
