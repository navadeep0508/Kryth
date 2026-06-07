"""KRYTH Mission Graph & Workspace Intelligence System.

Transforms KRYTH from an autonomous executor into a persistent engineering
operating system — maintaining a live mission DAG, workspace entity model,
artifact history, and full subsystem checkpoints.

Quick start::

    from agent.mission import MissionManager

    mgr = MissionManager(cwd=".")
    mission_id = mgr.start("Build Auth Service", goal="Implement JWT auth")
    mgr.add_task(mission_id, {"id": "t1", "description": "Create JWT middleware"})
    mgr.finish_task(mission_id, "t1", result={"file": "middleware/auth.py"})
    print(mgr.summary(mission_id))
"""

from agent.mission.mission_manager import MissionManager
from agent.mission.mission_dag import MissionDAG
from agent.mission.workspace_model import WorkspaceModel
from agent.mission.artifact_tracker import ArtifactTracker
from agent.mission.dependency_engine import DependencyEngine, ImpactResult
from agent.mission.checkpoint_engine import CheckpointEngine
from agent.mission.impact_analyzer import ImpactAnalyzer, ImpactReport
from agent.mission.mission_analytics import MissionAnalytics
from agent.mission.mission_memory_bridge import MissionMemoryBridge
from agent.mission.config import MissionStatus, NodeStatus, ArtifactKind, EntityKind

__all__ = [
    "MissionManager",
    "MissionDAG",
    "WorkspaceModel",
    "ArtifactTracker",
    "DependencyEngine",
    "ImpactResult",
    "CheckpointEngine",
    "ImpactAnalyzer",
    "ImpactReport",
    "MissionAnalytics",
    "MissionMemoryBridge",
    "MissionStatus",
    "NodeStatus",
    "ArtifactKind",
    "EntityKind",
]
