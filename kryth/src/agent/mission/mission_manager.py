"""MissionManager — single façade over all mission subsystems.

This is the main entry point for KRYTH's persistent engineering OS layer.
It composes MissionDAG, WorkspaceModel, ArtifactTracker, DependencyEngine,
CheckpointEngine, ImpactAnalyzer, MissionAnalytics, and MissionMemoryBridge.

Usage::

    from agent.mission import MissionManager

    mgr = MissionManager(project_hash="abc123def456", cwd=".")
    mission_id = mgr.start("Build Auth", goal="Implement JWT auth for the API")
    mgr.add_task(mission_id, {"id": "t1", "description": "Create JWT middleware", ...})
    mgr.finish_task(mission_id, "t1", result={"file": "middleware/auth.py"})
    print(mgr.summary(mission_id))
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from agent.mission.config import MissionStatus, NodeStatus
from agent.mission.mission_dag import MissionDAG
from agent.mission.workspace_model import WorkspaceModel
from agent.mission.artifact_tracker import ArtifactTracker
from agent.mission.dependency_engine import DependencyEngine
from agent.mission.checkpoint_engine import CheckpointEngine
from agent.mission.impact_analyzer import ImpactAnalyzer, ImpactReport
from agent.mission.mission_analytics import MissionAnalytics
from agent.mission.mission_memory_bridge import MissionMemoryBridge

logger = logging.getLogger(__name__)


class MissionManager:
    """Persistent engineering operating system for KRYTH.

    Parameters
    ----------
    project_hash:
        12-char project identity from ``persistence.project_hash_for()``.
    cwd:
        Working directory (used to derive project_hash if not provided).
    memory_manager:
        Optional MemoryManager instance for memory layer integration.
    db_path:
        Override SQLite path (for tests).
    """

    def __init__(
        self,
        project_hash: str = "",
        cwd: str = ".",
        memory_manager: Any = None,
        db_path: Optional[Path] = None,
    ) -> None:
        if not project_hash:
            try:
                from agent.persistence import project_hash_for
                project_hash = project_hash_for(cwd)
            except Exception:
                project_hash = "default"

        self.project_hash = project_hash
        self.cwd = cwd
        self._mm = memory_manager

        # Core subsystems (lazy-initialized via properties below)
        self._dag: Optional[MissionDAG] = None
        self._workspace: Optional[WorkspaceModel] = None
        self._artifacts: Optional[ArtifactTracker] = None
        self._deps: Optional[DependencyEngine] = None
        self._ckpt: Optional[CheckpointEngine] = None
        self._impact: Optional[ImpactAnalyzer] = None
        self._analytics: Optional[MissionAnalytics] = None
        self._bridge: Optional[MissionMemoryBridge] = None
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Subsystem properties (lazy)
    # ------------------------------------------------------------------

    @property
    def dag(self) -> MissionDAG:
        if self._dag is None:
            self._dag = MissionDAG(self.project_hash, db_path=self._db_path)
        return self._dag

    @property
    def workspace(self) -> WorkspaceModel:
        if self._workspace is None:
            self._workspace = WorkspaceModel(self.project_hash)
        return self._workspace

    @property
    def artifacts(self) -> ArtifactTracker:
        if self._artifacts is None:
            self._artifacts = ArtifactTracker(self.project_hash)
        return self._artifacts

    @property
    def deps(self) -> DependencyEngine:
        if self._deps is None:
            self._deps = DependencyEngine(
                artifact_tracker=self.artifacts,
                mission_dag=self.dag,
            )
        return self._deps

    @property
    def checkpoints(self) -> CheckpointEngine:
        if self._ckpt is None:
            self._ckpt = CheckpointEngine(
                project_hash=self.project_hash,
                mission_dag=self.dag,
            )
        return self._ckpt

    @property
    def impact(self) -> ImpactAnalyzer:
        if self._impact is None:
            self._impact = ImpactAnalyzer(
                dep_engine=self.deps,
                mission_dag=self.dag,
            )
        return self._impact

    @property
    def analytics(self) -> MissionAnalytics:
        if self._analytics is None:
            self._analytics = MissionAnalytics(
                dag=self.dag,
                checkpoint_engine=self.checkpoints,
            )
        return self._analytics

    @property
    def memory(self) -> MissionMemoryBridge:
        if self._bridge is None:
            self._bridge = MissionMemoryBridge(
                memory_manager=self._mm,
                project_hash=self.project_hash,
            )
        return self._bridge

    # ------------------------------------------------------------------
    # Mission lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        name: str,
        goal: str = "",
        tasks: list[dict] | None = None,
    ) -> str:
        """Start a new named mission. Returns the mission_id."""
        mission_id = self.dag.create(name=name, goal=goal)
        self.dag.update_status(mission_id, MissionStatus.ACTIVE)
        self.memory.record_mission_start(mission_id, name, goal)

        if tasks:
            for t in tasks:
                self.dag.add_node(mission_id, t)

        logger.info("Mission started: %s [%s]", name, mission_id)
        return mission_id

    def load(self, mission_id: str) -> dict:
        """Load mission state. Returns mission dict + progress."""
        mission = self.dag.get(mission_id)
        if mission is None:
            raise KeyError(f"Mission {mission_id!r} not found")
        mission["progress"] = self.dag.progress(mission_id)
        mission["nodes"] = self.dag.get_nodes(mission_id)
        return mission

    def resume(self, mission_id: str | None = None) -> dict:
        """Resume an incomplete mission (latest if no id provided).

        Returns the loaded mission state.
        """
        if mission_id is None:
            resumable = self.dag.get_resumable()
            if not resumable:
                return {"error": "No resumable missions found", "missions": []}
            mission_id = resumable[0]["id"]

        self.dag.update_status(mission_id, MissionStatus.ACTIVE)
        logger.info("Mission resumed: %s", mission_id)
        return self.load(mission_id)

    def pause(self, mission_id: str) -> None:
        self.dag.update_status(mission_id, MissionStatus.PAUSED)
        logger.info("Mission paused: %s", mission_id)

    def complete(self, mission_id: str, summary: str = "") -> None:
        """Mark mission complete and record experience."""
        mission = self.dag.get(mission_id)
        if mission is None:
            return
        self.dag.update_status(mission_id, MissionStatus.COMPLETED, completed_at=time.time())

        nodes = self.dag.get_nodes(mission_id)
        task_names = [n["description"] or n["id"] for n in nodes]
        duration = (time.time() - mission.get("created_at", time.time()))
        self.memory.record_mission_complete(
            mission_id, mission["name"], mission["goal"],
            task_names, success=True, duration=duration,
        )
        logger.info("Mission completed: %s", mission_id)

    def abort(self, mission_id: str, reason: str = "") -> None:
        """Abort a mission without recording success."""
        self.dag.update_status(mission_id, MissionStatus.ABORTED, completed_at=time.time())
        if reason:
            self.dag.update_metadata(mission_id, {"abort_reason": reason})
        logger.info("Mission aborted: %s — %s", mission_id, reason)

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def add_task(self, mission_id: str, node: dict) -> str:
        """Add a task node to an existing mission. Returns node_id."""
        if "id" not in node:
            import uuid
            node["id"] = str(uuid.uuid4())[:8]
        self.dag.add_node(mission_id, node)
        return node["id"]

    def update_task(self, mission_id: str, node_id: str, **kwargs: Any) -> None:
        self.dag.update_node(mission_id, node_id, **kwargs)

    def block_task(self, mission_id: str, node_id: str, reason: str = "") -> None:
        self.dag.update_node(mission_id, node_id, status=NodeStatus.BLOCKED.value, error=reason)

    def finish_task(
        self,
        mission_id: str,
        node_id: str,
        result: Any = None,
        artifacts: list[dict] | None = None,
    ) -> None:
        """Mark task complete and optionally record artifacts."""
        self.dag.update_node(
            mission_id, node_id,
            status=NodeStatus.COMPLETED.value,
            result=result,
        )
        node = self.dag.get_node(mission_id, node_id)
        description = node["description"] if node else node_id
        self.memory.record_task_complete(mission_id, node_id, description, success=True)

        if artifacts:
            for art in artifacts:
                self.artifacts.record(
                    mission_id, node_id,
                    path=art.get("path", ""),
                    kind=art.get("kind", "file"),
                    name=art.get("name", ""),
                    operation=art.get("operation", "created"),
                )

    # ------------------------------------------------------------------
    # Status & display
    # ------------------------------------------------------------------

    def status(self, mission_id: str) -> dict:
        """Return a status dict with progress and node breakdown."""
        mission = self.dag.get(mission_id)
        if mission is None:
            return {"error": f"Mission {mission_id!r} not found"}
        progress = self.dag.progress(mission_id)
        nodes = self.dag.get_nodes(mission_id)
        return {
            "mission_id": mission_id,
            "name": mission["name"],
            "goal": mission["goal"],
            "status": mission["status"],
            "progress": progress,
            "nodes": [
                {"id": n["id"], "status": n["status"], "description": n.get("description", "")}
                for n in nodes
            ],
        }

    def summary(self, mission_id: str) -> str:
        """Return a Markdown progress dashboard."""
        return self.analytics.export_summary(mission_id)

    def list_missions(self) -> list[dict]:
        return self.dag.list_missions()

    # ------------------------------------------------------------------
    # Checkpoint convenience
    # ------------------------------------------------------------------

    def checkpoint(self, mission_id: str, label: str = "") -> Path:
        path = self.checkpoints.save(mission_id, label=label, memory_manager=self._mm)
        self.memory.record_checkpoint(mission_id, str(path))
        return path

    def impact_analysis(
        self,
        changed_paths: list[str],
        mission_id: str | None = None,
    ) -> ImpactReport:
        return self.impact.analyze(changed_paths, root=self.cwd, mission_id=mission_id)
