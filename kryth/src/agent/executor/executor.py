"""Main executor — runs task graphs by dispatching to the action runner.

Ties together the TaskGraph, ActionRunner, StateManager, and Validator
into a cohesive execution pipeline.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.executor.action_runner import ActionRunner
from agent.executor.state_manager import StateManager
from agent.executor.validator import Validator
from agent.task_graph import TaskGraph, TaskNode, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class ExecutorResult:
    """Result of executing a task graph."""

    success: bool = False
    goal: str = ""
    results: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    progress: dict = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "goal": self.goal,
            "results": {k: str(v)[:500] for k, v in self.results.items()},
            "errors": self.errors,
            "progress": self.progress,
            "elapsed_seconds": self.elapsed_seconds,
            "summary": self.summary,
        }


class Executor:
    """Top-level executor that runs task graphs.

    The executor orchestrates the full execution pipeline:
    1. Takes a plan (from the planner) or a TaskGraph
    2. Validates the graph structure
    3. Executes tasks in dependency order via ActionRunner
    4. Tracks state in StateManager
    5. Validates results via Validator
    6. Returns a comprehensive ExecutorResult

    Usage:
        executor = Executor()
        result = executor.run_plan(plan_dict)
        # or
        result = executor.run_graph(task_graph)
    """

    def __init__(
        self,
        action_runner: ActionRunner | None = None,
        state_manager: StateManager | None = None,
        validator: Validator | None = None,
    ) -> None:
        self.action_runner = action_runner or ActionRunner()
        self.state_manager = state_manager or StateManager()
        self.validator = validator or Validator()

    def run_plan(self, plan: dict) -> ExecutorResult:
        """Execute a plan from the planner directly.

        The plan dict should have a 'tasks' key (or 'steps' for legacy)
        with the list of task definitions.
        """
        if "tasks" not in plan and "steps" not in plan:
            return ExecutorResult(
                success=False,
                summary="Plan has no 'tasks' or 'steps'",
            )

        graph = TaskGraph.from_plan(plan)
        return self.run_graph(graph, goal=plan.get("goal", ""))

    def run_graph(
        self,
        graph: TaskGraph,
        *,
        goal: str = "",
        max_concurrency: int = 4,
    ) -> ExecutorResult:
        """Execute a TaskGraph.

        Args:
            graph: The TaskGraph to execute.
            goal: Optional goal description.
            max_concurrency: Maximum parallel execution.

        Returns:
            ExecutorResult with execution details.
        """
        start_time = time.time()
        self.state_manager.start_execution()

        errors = graph.validate()
        if errors:
            return ExecutorResult(
                success=False,
                goal=goal,
                summary=f"Graph validation failed: {'; '.join(errors)}",
            )

        results: dict[str, Any] = {}
        task_errors: dict[str, str] = {}

        def on_node_start(node: TaskNode) -> None:
            self.state_manager.start_step(node.id)
            logger.info(
                "[%s] %s (%s/%s) %s",
                node.id, node.description or node.action,
                node.tool, node.action,
                json.dumps(node.params)[:200],
            )

        def on_node_complete(node: TaskNode) -> None:
            if node.status == TaskStatus.SUCCESS:
                self.state_manager.complete_step(node.id, node.result)
                results[node.id] = node.result
                logger.info("[%s] OK (%.1fs)", node.id, node.duration)
            elif node.status == TaskStatus.FAILED:
                self.state_manager.fail_step(node.id, node.error)
                task_errors[node.id] = node.error
                logger.warning("[%s] FAILED: %s", node.id, node.error)

        def task_runner(node: TaskNode) -> Any:
            return self.action_runner.run(
                node.tool,
                node.action,
                node.params,
            )

        try:
            graph.execute(
                task_runner,
                on_node_start=on_node_start,
                on_node_complete=on_node_complete,
                max_concurrency=max_concurrency,
            )
        except Exception as e:
            logger.error("Executor error: %s", e)
            task_errors["_executor"] = str(e)

        elapsed = time.time() - start_time
        progress = graph.progress
        success = progress["failed"] == 0 and progress["total"] > 0

        return ExecutorResult(
            success=success,
            goal=goal,
            results=results,
            errors=task_errors,
            progress=progress,
            elapsed_seconds=elapsed,
            summary=self._build_summary(goal, progress, elapsed),
        )

    def run_single_task(
        self,
        tool: str,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a single tool action without a graph.

        Useful for simple one-shot operations or testing.
        """
        return self.action_runner.run(tool, action, params)

    def register_action(
        self,
        tool: str,
        action: str,
        handler: callable,
    ) -> None:
        """Register a custom action handler."""
        self.action_runner.register_action(tool, action, handler)

    def _build_summary(
        self,
        goal: str,
        progress: dict,
        elapsed: float,
    ) -> str:
        """Build a human-readable summary of execution."""
        if not progress.get("total"):
            return "No tasks executed."

        parts = [f"Goal: {goal or '(unspecified)'}"]

        completed = progress.get("completed", 0)
        failed = progress.get("failed", 0)
        total = progress.get("total", 0)

        if failed == 0:
            parts.append(f"All {completed}/{total} tasks completed successfully")
        else:
            parts.append(f"{completed}/{total} completed, {failed} failed")

        parts.append(f"Took {elapsed:.1f}s")

        if progress.get("running"):
            parts.append(f"({progress['running']} still running)")

        return " | ".join(parts)
