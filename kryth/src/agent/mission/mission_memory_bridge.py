"""MissionMemoryBridge — route mission lifecycle events into KRYTH memory layers.

Reuses:
- MemoryManager (LAYER_EXECUTION, LAYER_WORKFLOW, LAYER_WORKING)
- ExperienceStore (kind="mission")
- WorkflowMemory.register_workflow()
- ReflectionEngine (if available)
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class MissionMemoryBridge:
    """Connect mission lifecycle events to all KRYTH memory subsystems.

    Parameters
    ----------
    memory_manager:
        An instance of ``agent.memory.memory_manager.MemoryManager``.
        All methods are no-ops if None.
    project_hash:
        Project identity for experience and workflow stores.
    """

    def __init__(
        self,
        memory_manager: Any = None,
        project_hash: str = "",
    ) -> None:
        self._mm = memory_manager
        self._project_hash = project_hash

    # ------------------------------------------------------------------
    # Mission lifecycle
    # ------------------------------------------------------------------

    def record_mission_start(self, mission_id: str, name: str, goal: str) -> None:
        if self._mm is None:
            return
        self._safe(
            lambda: self._mm.store(
                key=f"mission.active.{mission_id}",
                value={"name": name, "goal": goal, "started": time.time()},
                scope=self._layer("working"),
            )
        )

    def record_mission_complete(
        self,
        mission_id: str,
        name: str,
        goal: str,
        task_names: list[str],
        success: bool,
        duration: float,
    ) -> None:
        """Store experience + workflow pattern on mission completion."""
        # ExperienceStore
        self._safe(lambda: self._store_experience(
            mission_id, name, goal, task_names, success, duration
        ))
        # WorkflowMemory
        if task_names:
            self._safe(lambda: self._register_workflow(name, task_names, goal, success))
        # MemoryManager execution layer
        if self._mm is not None:
            self._safe(lambda: self._mm.store(
                key=f"mission.complete.{mission_id}",
                value={"name": name, "success": success, "duration": duration},
                scope=self._layer("execution"),
            ))

    def record_task_complete(
        self,
        mission_id: str,
        task_id: str,
        description: str,
        success: bool,
    ) -> None:
        if self._mm is None:
            return
        self._safe(lambda: self._mm.store(
            key=f"mission.task.{task_id}",
            value={"mission": mission_id, "description": description, "success": success, "time": time.time()},
            scope=self._layer("workflow"),
        ))

    def record_checkpoint(self, mission_id: str, checkpoint_path: str) -> None:
        if self._mm is None:
            return
        self._safe(lambda: self._mm.store(
            key=f"mission.checkpoint.{mission_id}",
            value={"path": checkpoint_path, "time": time.time()},
            scope=self._layer("working"),
        ))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _store_experience(
        self,
        mission_id: str,
        name: str,
        goal: str,
        task_names: list[str],
        success: bool,
        duration: float,
    ) -> None:
        try:
            from agent.experience.store import get_store
            store = get_store(self._project_hash or "global")
            store.insert(
                kind="mission",
                title=name,
                summary=f"Goal: {goal}. Tasks: {len(task_names)}. Duration: {duration:.0f}s.",
                tags=["mission", "workflow"],
                outcome="success" if success else "failure",
                importance=0.7,
                confidence=0.9 if success else 0.5,
                extra={"mission_id": mission_id, "task_count": len(task_names)},
            )
        except Exception as exc:
            logger.debug("record experience: %s", exc)

    def _register_workflow(
        self,
        name: str,
        task_names: list[str],
        goal: str,
        success: bool,
    ) -> None:
        try:
            from agent.memory.workflow_memory import get_workflow_memory
            wm = get_workflow_memory(project_hash=self._project_hash)
            wid = wm.register_workflow(
                name=name,
                steps=task_names,
                intent_type=_infer_intent(goal),
            )
            wm.record_run(
                workflow_id=wid,
                outcome="success" if success else "failure",
            )
        except Exception as exc:
            logger.debug("register workflow: %s", exc)

    def _layer(self, name: str) -> str:
        try:
            from agent.memory.memory_manager import (
                LAYER_WORKING, LAYER_EXECUTION, LAYER_WORKFLOW,
            )
            return {"working": LAYER_WORKING, "execution": LAYER_EXECUTION, "workflow": LAYER_WORKFLOW}.get(name, LAYER_WORKING)
        except ImportError:
            return name

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as exc:
            logger.debug("MissionMemoryBridge error: %s", exc)


def _infer_intent(goal: str) -> str:
    g = goal.lower()
    for keyword, intent in [
        ("test", "testing"), ("deploy", "deployment"), ("build", "build"),
        ("fix", "repair"), ("refactor", "refactor"), ("api", "api"),
        ("frontend", "frontend"), ("backend", "backend"), ("database", "database"),
    ]:
        if keyword in g:
            return intent
    return "general"
