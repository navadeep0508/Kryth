"""Action Manager — top-level UAL entry point.

Converts a user mission into an ActionGraph, runs it through the
ActionScheduler, and returns a structured result.

The Manager is the kernel interface for KRYTH's new action-oriented
thinking. All higher-level systems (planner, orchestration, agent_loop)
talk to the UAL through ActionManager.

Usage::

    manager = ActionManager()

    # From a plan dict produced by the LLM
    result = manager.run_plan({
        "mission": "Deploy my application",
        "actions": [
            {"id": "build",  "goal": "Build backend",   "priority": 10,
             "preferred_executors": ["terminal"],
             "params": {"command": "make build"},
             "verification_steps": ["check_exit_0"],
             "rollback_steps": ["run:make clean"],
             "dependencies": []},
            {"id": "test",   "goal": "Run tests",        "priority": 20,
             "preferred_executors": ["terminal"],
             "params": {"command": "pytest"},
             "dependencies": ["build"]},
            {"id": "deploy", "goal": "Deploy via API",   "priority": 30,
             "preferred_executors": ["api"],
             "params": {"url": "https://...", "method": "POST"},
             "dependencies": ["test"]},
        ]
    })

    # Or from a raw ActionGraph you built in code
    result = manager.run_graph(graph)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Optional

from agent.action.action_graph import ActionGraph
from agent.action.action_memory import ActionMemory
from agent.action.action_registry import ActionRegistry, get_registry
from agent.action.action_scheduler import ActionScheduler, SchedulerResult
from agent.action.action_analytics import ActionAnalytics

logger = logging.getLogger(__name__)


class ActionManager:
    """Top-level interface to the Universal Action Layer."""

    def __init__(
        self,
        project_root: str | Path = ".",
        registry: Optional[ActionRegistry] = None,
        max_workers: int = 8,
        on_progress: Optional[Callable] = None,
    ) -> None:
        self._root      = Path(project_root).resolve()
        self._registry  = registry or get_registry()
        self._memory    = ActionMemory(self._root)
        self._analytics = ActionAnalytics(self._root)
        self._scheduler = ActionScheduler(
            registry=self._registry,
            memory=self._memory,
            analytics=self._analytics,
            max_workers=max_workers,
            on_progress=on_progress,
        )

    # ── Public API ────────────────────────────────────────────────────

    def run_plan(self, plan: dict[str, Any]) -> SchedulerResult:
        """Build an ActionGraph from a plan dict and execute it."""
        graph = ActionGraph.from_plan(plan)
        errors = graph.validate()
        if errors:
            logger.error("ActionGraph validation failed: %s", errors)
            from agent.action.action_scheduler import SchedulerResult
            return SchedulerResult(
                success=False,
                mission=plan.get("mission", ""),
                analytics={"validation_errors": errors},
            )
        logger.info(
            "ActionManager: running plan '%s' (%d actions)",
            graph.mission, len(graph.actions),
        )
        return self._scheduler.run(graph)

    def run_graph(self, graph: ActionGraph) -> SchedulerResult:
        """Execute a pre-built ActionGraph."""
        logger.info(
            "ActionManager: running graph '%s' (%d actions)",
            graph.mission, len(graph.actions),
        )
        return self._scheduler.run(graph)

    def register_executor(self, executor) -> None:
        """Plug in a new executor without touching core code."""
        self._registry.register(executor)
        logger.info("Registered executor: %s", executor.name)

    def analytics_summary(self) -> dict:
        return self._analytics.summary()

    def export_analytics(self, path: Optional[str] = None) -> str:
        return self._analytics.export_json(path)

    def memory_stats(self) -> dict:
        return self._memory.executor_stats()
