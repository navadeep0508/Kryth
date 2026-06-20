"""Planner Dashboard — live ASCII panel for the Universal Planner.

Thread-safe state store + ASCII renderer, suitable for embedding in the
existing ui/dashboard.py or for standalone terminal output.

Events: push_planner_event(event, **kwargs)
  "started"     — new planning session
  "complexity"  — complexity estimate available
  "decomposed"  — raw actions produced
  "graph_ready" — ActionGraph validated and ready
  "replanned"   — dynamic replan occurred
  "completed"   — mission finished
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class PlannerPanelState:
    session_id: str = ""
    mission: str = ""
    complexity: str = ""
    worker_count: int = 0
    action_count: int = 0
    parallel_layers: int = 0
    current_layer: int = 0
    replan_count: int = 0
    status: str = "idle"
    started_at: float = 0.0
    estimated_duration_s: float = 0.0
    completed: bool = False
    success: Optional[bool] = None


_state = PlannerPanelState()
_lock  = threading.Lock()


def push_planner_event(event: str, **kwargs: Any) -> None:
    """Update planner dashboard state from any thread."""
    with _lock:
        if event == "started":
            _state.session_id        = kwargs.get("session_id", "")
            _state.mission           = kwargs.get("mission", "")
            _state.status            = "planning"
            _state.started_at        = time.time()
            _state.replan_count      = 0
            _state.completed         = False
            _state.success           = None
            _state.current_layer     = 0

        elif event == "complexity":
            _state.complexity          = kwargs.get("complexity", "")
            _state.worker_count        = kwargs.get("workers", 0)
            _state.estimated_duration_s = kwargs.get("estimated_duration_s", 0.0)

        elif event == "decomposed":
            _state.action_count = kwargs.get("action_count", 0)

        elif event == "graph_ready":
            _state.parallel_layers = kwargs.get("parallel_layers", 0)
            _state.action_count    = kwargs.get("action_count", _state.action_count)
            _state.status          = "executing"

        elif event == "layer_started":
            _state.current_layer = kwargs.get("layer", _state.current_layer + 1)

        elif event == "replanned":
            _state.replan_count += 1
            _state.status        = "replanning"

        elif event == "completed":
            _state.completed = True
            _state.success   = kwargs.get("success", False)
            _state.status    = "done" if _state.success else "failed"


def render_planner_panel(width: int = 50) -> str:
    """Return ASCII panel string for embedding in the dashboard."""
    with _lock:
        s = PlannerPanelState(**_state.__dict__)

    sep   = "-" * width
    lines = [
        "=" * width,
        "  UNIVERSAL PLANNER".center(width),
        "=" * width,
    ]

    if not s.mission:
        lines.append("  (idle — awaiting mission)")
        lines.append("=" * width)
        return "\n".join(lines)

    elapsed = time.time() - s.started_at if s.started_at else 0
    remaining = max(0, s.estimated_duration_s - elapsed) if s.estimated_duration_s else 0

    def fmt_time(secs: float) -> str:
        m, sc = divmod(int(secs), 60)
        return f"{m:02d}:{sc:02d}"

    lines += [
        f"  Mission   : {s.mission[:width-14]}",
        f"  Status    : {s.status.upper()}",
        sep,
        f"  Complexity: {s.complexity or '?':<10}  Workers: {s.worker_count}",
        f"  Actions   : {s.action_count:<10}  Layers:  {s.parallel_layers}",
        f"  Layer     : {s.current_layer}/{s.parallel_layers or '?':<8}  Replans: {s.replan_count}",
    ]

    if s.estimated_duration_s:
        lines.append(f"  Estimated : {fmt_time(s.estimated_duration_s)}       Remaining: {fmt_time(remaining)}")

    if s.completed:
        verdict = "[SUCCESS]" if s.success else "[FAILED]"
        lines.append(sep)
        lines.append(f"  {verdict}  Elapsed: {fmt_time(elapsed)}")

    lines.append("=" * width)
    return "\n".join(lines)


def get_planner_state() -> PlannerPanelState:
    with _lock:
        return PlannerPanelState(**_state.__dict__)
