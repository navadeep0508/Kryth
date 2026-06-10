"""MissionAnalytics — completion rates, durations, retries, rollback counts.

Reads from MissionDAG SQLite. No additional storage.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.mission.mission_dag import MissionDAG
    from agent.mission.checkpoint_engine import CheckpointEngine


class MissionAnalytics:
    """Compute metrics for missions and projects.

    Parameters
    ----------
    dag:
        MissionDAG instance.
    checkpoint_engine:
        CheckpointEngine instance (for checkpoint counts).
    """

    def __init__(
        self,
        dag: "MissionDAG",
        checkpoint_engine: "CheckpointEngine | None" = None,
    ) -> None:
        self._dag = dag
        self._ckpt = checkpoint_engine

    # ------------------------------------------------------------------
    # Per-mission report
    # ------------------------------------------------------------------

    def mission_report(self, mission_id: str) -> dict[str, Any]:
        """Return a structured report for *mission_id*."""
        mission = self._dag.get(mission_id)
        if mission is None:
            return {"error": f"Mission {mission_id!r} not found"}

        progress = self._dag.progress(mission_id)
        nodes = self._dag.get_nodes(mission_id)

        # Timing
        elapsed = 0.0
        if mission.get("created_at"):
            end = mission.get("completed_at") or time.time()
            elapsed = end - mission["created_at"]

        # Retry count
        total_retries = sum(n.get("retry_count", 0) for n in nodes)

        # Node timing breakdown
        durations: list[float] = []
        for n in nodes:
            if n.get("started_at") and n.get("completed_at"):
                durations.append(n["completed_at"] - n["started_at"])
        avg_node_duration = sum(durations) / len(durations) if durations else 0.0

        # Checkpoint count
        ckpt_count = 0
        if self._ckpt:
            ckpt_count = len(self._ckpt.list_checkpoints(mission_id))

        return {
            "mission_id": mission_id,
            "name": mission.get("name", ""),
            "status": mission.get("status", ""),
            "goal": mission.get("goal", ""),
            "progress_percent": progress["percent"],
            "tasks_total": progress["total"],
            "tasks_completed": progress["completed"],
            "tasks_failed": progress["failed"],
            "tasks_pending": progress["pending"],
            "elapsed_seconds": round(elapsed, 1),
            "total_retries": total_retries,
            "avg_node_duration_seconds": round(avg_node_duration, 2),
            "checkpoints": ckpt_count,
        }

    def project_report(self) -> dict[str, Any]:
        """Cross-mission summary for the project."""
        missions = self._dag.list_missions()
        total = len(missions)
        if total == 0:
            return {"total_missions": 0}

        completed = sum(1 for m in missions if m["status"] == "completed")
        failed = sum(1 for m in missions if m["status"] in ("failed", "aborted"))
        completion_rate = round(completed / total * 100, 1) if total else 0.0

        # Collect all nodes for retry/failure stats
        total_retries = 0
        total_nodes = 0
        for m in missions:
            nodes = self._dag.get_nodes(m["id"])
            total_nodes += len(nodes)
            total_retries += sum(n.get("retry_count", 0) for n in nodes)

        total_checkpoints = 0
        if self._ckpt:
            for m in missions:
                total_checkpoints += len(self._ckpt.list_checkpoints(m["id"]))

        return {
            "project_hash": self._dag.project_hash,
            "total_missions": total,
            "completed_missions": completed,
            "failed_missions": failed,
            "completion_rate_percent": completion_rate,
            "total_task_nodes": total_nodes,
            "total_retries": total_retries,
            "total_checkpoints": total_checkpoints,
        }

    # ------------------------------------------------------------------
    # Markdown export
    # ------------------------------------------------------------------

    def export_summary(self, mission_id: str) -> str:
        """Return a Markdown dashboard string for *mission_id*."""
        r = self.mission_report(mission_id)
        if "error" in r:
            return f"# Mission {mission_id}\n\n{r['error']}"

        pct = r["progress_percent"]
        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        lines = [
            f"# Mission: {r['name']}",
            f"\n**Goal:** {r['goal']}",
            f"\n**Status:** {r['status']}  |  **Progress:** {bar} {pct}%",
            f"\n| Tasks | Total | Done | Failed | Pending |",
            f"|-------|-------|------|--------|---------|",
            f"| | {r['tasks_total']} | {r['tasks_completed']} | {r['tasks_failed']} | {r['tasks_pending']} |",
            f"\n**Elapsed:** {r['elapsed_seconds']}s  |  "
            f"**Retries:** {r['total_retries']}  |  "
            f"**Checkpoints:** {r['checkpoints']}",
        ]

        # Node list
        nodes = self._dag.get_nodes(mission_id)
        if nodes:
            lines.append("\n## Tasks\n")
            icons = {
                "completed": "✓", "failed": "✗", "active": "◉",
                "pending": "○", "blocked": "⊘", "retrying": "↻", "skipped": "⤳",
            }
            for n in nodes:
                icon = icons.get(n["status"], "?")
                lines.append(f"- {icon} **{n['id']}**: {n['description'] or n['action']}")

        return "\n".join(lines)
