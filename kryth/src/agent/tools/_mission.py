"""Tool implementations for the Mission Graph & Workspace Intelligence system."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _mgr(cwd: str = ".") -> "MissionManager":  # noqa: F821
    from agent.mission.mission_manager import MissionManager
    return MissionManager(cwd=cwd)


# ------------------------------------------------------------------
# Tool handlers
# ------------------------------------------------------------------


def mission_start(
    name: str,
    goal: str = "",
    tasks: list[dict] | None = None,
) -> str:
    """Start a new persistent mission."""
    try:
        mgr = _mgr()
        mission_id = mgr.start(name=name, goal=goal, tasks=tasks)
        return json.dumps({
            "status": "started",
            "mission_id": mission_id,
            "name": name,
            "goal": goal,
            "task_count": len(tasks) if tasks else 0,
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def mission_resume(mission_id: str = "") -> str:
    """Resume the latest (or specified) incomplete mission."""
    try:
        mgr = _mgr()
        result = mgr.resume(mission_id or None)
        if "error" in result:
            return json.dumps(result)
        progress = result.get("progress", {})
        pending_nodes = [n for n in result.get("nodes", []) if n["status"] in ("pending", "active", "blocked")]
        return json.dumps({
            "status": "resumed",
            "mission_id": result["id"],
            "name": result["name"],
            "goal": result.get("goal", ""),
            "progress_percent": progress.get("percent", 0),
            "pending_tasks": [{"id": n["id"], "description": n.get("description", "")} for n in pending_nodes],
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def mission_status(mission_id: str) -> str:
    """Get current status of a mission."""
    try:
        mgr = _mgr()
        status = mgr.status(mission_id)
        return json.dumps(status)
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def mission_add_task(
    mission_id: str,
    description: str,
    task_id: str = "",
    depends_on: list[str] | None = None,
    tool: str = "",
    action: str = "",
    params: dict | None = None,
) -> str:
    """Add a new task to a running mission."""
    try:
        mgr = _mgr()
        node: dict[str, Any] = {
            "description": description,
            "depends_on": depends_on or [],
            "tool": tool,
            "action": action,
            "params": params or {},
        }
        if task_id:
            node["id"] = task_id
        added_id = mgr.add_task(mission_id, node)
        return json.dumps({"status": "added", "mission_id": mission_id, "task_id": added_id})
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def mission_finish_task(
    mission_id: str,
    task_id: str,
    result: dict | None = None,
    artifacts: list[dict] | None = None,
) -> str:
    """Mark a task complete and optionally record artifacts."""
    try:
        mgr = _mgr()
        mgr.finish_task(mission_id, task_id, result=result, artifacts=artifacts)
        progress = mgr.dag.progress(mission_id)
        return json.dumps({
            "status": "completed",
            "mission_id": mission_id,
            "task_id": task_id,
            "artifacts_recorded": len(artifacts) if artifacts else 0,
            "progress_percent": progress["percent"],
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def mission_checkpoint(mission_id: str, label: str = "") -> str:
    """Save a full subsystem checkpoint."""
    try:
        mgr = _mgr()
        path = mgr.checkpoint(mission_id, label=label)
        checkpoints = mgr.checkpoints.list_checkpoints(mission_id)
        return json.dumps({
            "status": "saved",
            "mission_id": mission_id,
            "checkpoint_path": str(path),
            "total_checkpoints": len(checkpoints),
        })
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def mission_impact_analysis(
    changed_paths: list[str],
    mission_id: str = "",
) -> str:
    """Analyze downstream impact of changed files."""
    try:
        mgr = _mgr()
        report = mgr.impact_analysis(
            changed_paths=changed_paths,
            mission_id=mission_id or None,
        )
        return json.dumps(report.to_dict())
    except Exception as exc:
        return json.dumps({"status": "error", "error": str(exc)})


def mission_summary(mission_id: str) -> str:
    """Return a Markdown mission progress dashboard."""
    try:
        mgr = _mgr()
        return mgr.summary(mission_id)
    except Exception as exc:
        return f"[mission_summary error] {exc}"


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------

MISSION_TOOLS: dict[str, Any] = {
    "mission_start": mission_start,
    "mission_resume": mission_resume,
    "mission_status": mission_status,
    "mission_add_task": mission_add_task,
    "mission_finish_task": mission_finish_task,
    "mission_checkpoint": mission_checkpoint,
    "mission_impact_analysis": mission_impact_analysis,
    "mission_summary": mission_summary,
}
