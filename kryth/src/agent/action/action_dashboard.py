"""Universal Action Layer dashboard panel — integrates with existing ui/dashboard.py.

Adds a UAL panel to the live operations center showing:
  - Running actions + executor in use
  - Verification status per action
  - Rollback readiness
  - Current layer info
  - Completed / failed counts

Designed to be imported alongside ui/dashboard.py:
    from agent.action.action_dashboard import push_action_event, render_ual_panel
"""

from __future__ import annotations


import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ActionEntry:
    action_id: str
    goal: str
    executor: str
    status: str          # running | succeeded | failed | rolled_back | skipped
    verified: bool = False
    rollback_ready: bool = False
    duration_ms: float = 0.0
    error: str = ""
    started_at: float = field(default_factory=time.time)


@dataclass
class UALState:
    entries: dict[str, ActionEntry] = field(default_factory=dict)
    completed: int = 0
    failed: int = 0
    current_layer: int = 0
    mission: str = ""


# ── Module-level state ────────────────────────────────────────────────────────

_state = UALState()
_lock  = threading.Lock()
_log: Deque[str] = deque(maxlen=200)


def push_action_event(event: str, **kwargs: Any) -> None:
    """Thread-safe update from ActionScheduler callbacks.

    Events:
        "running"    — action started; kwargs: action_id, goal, executor
        "succeeded"  — action done;    kwargs: action_id, duration_ms, verified
        "failed"     — action failed;  kwargs: action_id, error, rolled_back
        "skipped"    — action skipped; kwargs: action_id
        "mission"    — new mission;    kwargs: name
    """
    with _lock:
        if event == "mission":
            _state.mission = kwargs.get("name", "")
            _state.entries.clear()
            _state.completed = 0
            _state.failed = 0
            _state.current_layer = 0
            _log.append(f"[UAL] Mission: {_state.mission}")

        elif event == "running":
            aid = kwargs["action_id"]
            _state.entries[aid] = ActionEntry(
                action_id=aid,
                goal=kwargs.get("goal", ""),
                executor=kwargs.get("executor", "?"),
                status="running",
                rollback_ready=bool(kwargs.get("has_rollback", False)),
            )
            _state.current_layer = kwargs.get("layer", _state.current_layer)
            _log.append(
                f"[{aid}] RUNNING via {kwargs.get('executor','?')}: "
                f"{kwargs.get('goal','')[:50]}"
            )

        elif event == "succeeded":
            aid = kwargs["action_id"]
            if aid in _state.entries:
                e = _state.entries[aid]
                e.status    = "succeeded"
                e.verified  = kwargs.get("verified", False)
                e.duration_ms = kwargs.get("duration_ms", 0.0)
            _state.completed += 1
            v = "verified" if kwargs.get("verified") else "unverified"
            _log.append(f"[{aid}] SUCCEEDED ({v})")

        elif event == "failed":
            aid = kwargs["action_id"]
            if aid in _state.entries:
                e = _state.entries[aid]
                e.status = "rolled_back" if kwargs.get("rolled_back") else "failed"
                e.error  = kwargs.get("error", "")
                e.duration_ms = kwargs.get("duration_ms", 0.0)
            _state.failed += 1
            _state.parallel_count = max(0, _state.parallel_count - 1)
            _log.append(f"[{aid}] FAILED: {kwargs.get('error','')[:80]}")

        elif event == "skipped":
            aid = kwargs.get("action_id", "?")
            if aid in _state.entries:
                _state.entries[aid].status = "skipped"
            _log.append(f"[{aid}] SKIPPED")


def render_ual_panel(width: int = 60) -> str:
    """Return an ASCII panel string for embedding in the existing dashboard."""
    with _lock:
        s = _state
        entries = list(s.entries.values())

    sep    = "-" * width
    lines  = [
        "=" * width,
        f"  UAL - Universal Action Layer".center(width),
        "=" * width,
        f"  Mission : {s.mission[:width-12] or '(none)'}",
        f"  Done    : {s.completed}   Failed: {s.failed}   "
        f"Parallel: {s.parallel_count} (peak {s.peak_parallel})",
        sep,
    ]

    if not entries:
        lines.append("  (no actions yet)")
    else:
        status_icon = {
            "running":     "[>]",
            "succeeded":   "[+]",
            "failed":      "[!]",
            "rolled_back": "[<]",
            "skipped":     "[-]",
        }
        for e in entries[-10:]:   # show last 10
            icon     = status_icon.get(e.status, "[?]")
            verified = "verified" if e.verified else ""
            rollback = "rollback-ready" if e.rollback_ready else ""
            tags     = " ".join(filter(None, [verified, rollback]))
            line     = (
                f"  {icon} {e.action_id[:8]}  "
                f"ex={e.executor:<8}  "
                f"{e.goal[:30]:<30}  "
                f"{tags}"
            )
            lines.append(line)

    lines.append(sep)
    return "\n".join(lines)


def get_state() -> UALState:
    """Return a snapshot of the current UAL state (for Textual widget use)."""
    with _lock:
        from copy import deepcopy
        return deepcopy(_state)


def recent_log(n: int = 20) -> list[str]:
    """Return the N most recent log lines."""
    with _lock:
        return list(_log)[-n:]
