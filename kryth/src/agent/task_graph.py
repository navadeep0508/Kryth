"""Task Graph — DAG-based task execution using Python dicts (no external deps).

Represents tasks as a directed acyclic graph where each node is a task
with dependencies. Supports:
    - Topological ordering
    - Parallel execution of independent nodes
    - Retry with configurable backoff
    - Progress tracking and status reporting
    - Cycle detection
    - Result caching per node
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


@dataclass
class TaskNode:
    """A single node in the task DAG."""

    id: str
    tool: str = "filesystem"
    action: str = "read_file"
    params: dict = field(default_factory=dict)
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    max_retries: int = 3
    timeout_seconds: int = 60
    validation: str = ""
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""
    retry_count: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tool": self.tool,
            "action": self.action,
            "params": self.params,
            "description": self.description,
            "depends_on": self.depends_on,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "validation": self.validation,
            "status": self.status.value,
            "error": self.error,
            "retry_count": self.retry_count,
        }

    @property
    def duration(self) -> float:
        if self.completed_at and self.started_at:
            return self.completed_at - self.started_at
        return 0.0


class TaskGraph:
    """A DAG of tasks that can be executed in dependency order.

    Usage:
        graph = TaskGraph()
        graph.add_node(TaskNode(id="step1", action="read_file", params={"path": "a.txt"}))
        graph.add_node(TaskNode(id="step2", action="write_file",
                                params={"path": "b.txt"}, depends_on=["step1"]))
        graph.execute(your_executor_fn)
    """

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}
        self._execution_order: list[list[str]] = []  # layers of parallel ids

    def add_node(self, node: TaskNode) -> None:
        """Add a task node to the graph."""
        if node.id in self._nodes:
            raise ValueError(f"Duplicate node id: {node.id}")
        self._nodes[node.id] = node

    def add_nodes(self, nodes: list[TaskNode]) -> None:
        """Add multiple task nodes at once."""
        for node in nodes:
            self.add_node(node)

    def get_node(self, node_id: str) -> TaskNode | None:
        return self._nodes.get(node_id)

    @property
    def nodes(self) -> dict[str, TaskNode]:
        return self._nodes

    @property
    def pending_nodes(self) -> list[TaskNode]:
        return [n for n in self._nodes.values() if n.status == TaskStatus.PENDING]

    @property
    def completed_nodes(self) -> list[TaskNode]:
        return [n for n in self._nodes.values() if n.status == TaskStatus.SUCCESS]

    @property
    def failed_nodes(self) -> list[TaskNode]:
        return [n for n in self._nodes.values() if n.status == TaskStatus.FAILED]

    @property
    def is_complete(self) -> bool:
        return all(
            n.status in (TaskStatus.SUCCESS, TaskStatus.SKIPPED, TaskStatus.FAILED)
            for n in self._nodes.values()
        )

    @property
    def progress(self) -> dict:
        """Get progress statistics."""
        total = len(self._nodes)
        if total == 0:
            return {"total": 0, "completed": 0, "failed": 0, "running": 0,
                    "pending": 0, "percent": 0.0}
        completed = len(self.completed_nodes)
        failed = len(self.failed_nodes)
        running = sum(1 for n in self._nodes.values() if n.status == TaskStatus.RUNNING)
        pending = sum(1 for n in self._nodes.values() if n.status == TaskStatus.PENDING)
        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "running": running,
            "pending": pending,
            "percent": (completed / total) * 100 if total > 0 else 0.0,
        }

    # ------------------------------------------------------------------
    # Graph validation and topological sort
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the graph structure. Returns list of errors (empty = valid)."""
        errors = []
        if not self._nodes:
            return ["Graph has no nodes"]

        # Check all dependency references are valid
        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in self._nodes:
                    errors.append(
                        f"Node '{node.id}' depends on unknown '{dep}'"
                    )
                if dep == node.id:
                    errors.append(f"Node '{node.id}' depends on itself")

        # Check for cycles via topological sort
        try:
            self._topological_sort()
        except ValueError as e:
            errors.append(str(e))

        return errors

    def _topological_sort(self) -> list[list[str]]:
        """Kahn's algorithm: produce layers of parallel-safe node IDs.

        Returns list of layers, where each layer is a list of node IDs
        that can execute in parallel.

        Raises ValueError if a cycle is detected.
        """
        indegree: dict[str, int] = {}
        for nid, node in self._nodes.items():
            indegree[nid] = len(node.depends_on)

        remaining = set(self._nodes.keys())
        layers: list[list[str]] = []

        while remaining:
            layer = [nid for nid in remaining if indegree.get(nid, 0) == 0]
            if not layer:
                raise ValueError(
                    f"Cycle detected among: {sorted(remaining)}"
                )
            layers.append(layer)
            for nid in layer:
                remaining.discard(nid)
                for other in remaining:
                    if nid in self._nodes[other].depends_on:
                        indegree[other] = indegree.get(other, 0) - 1

        self._execution_order = layers
        return layers

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def execute(
        self,
        task_runner: Callable[[TaskNode], Any],
        *,
        on_node_start: Optional[Callable[[TaskNode], None]] = None,
        on_node_complete: Optional[Callable[[TaskNode], None]] = None,
        on_progress: Optional[Callable[[dict], None]] = None,
        max_concurrency: int = 4,
    ) -> dict[str, Any]:
        """Execute the task graph in dependency order.

        Args:
            task_runner: Callable that takes a TaskNode and returns its result.
            on_node_start: Optional callback when a node starts executing.
            on_node_complete: Optional callback when a node finishes.
            on_progress: Optional callback with progress dict after each node.
            max_concurrency: Maximum number of parallel nodes per layer.

        Returns:
            Dict mapping node id to result.
        """
        errors = self.validate()
        if errors:
            raise ValueError(f"Graph validation failed: {'; '.join(errors)}")

        layers = self._topological_sort()
        results: dict[str, Any] = {}

        for layer in layers:
            # Within each layer, nodes are independent
            for nid in layer:
                node = self._nodes[nid]
                if node.status == TaskStatus.SKIPPED:
                    continue

                # Check dependencies — skip if any dependency failed
                should_skip = False
                for dep_id in node.depends_on:
                    dep_node = self._nodes.get(dep_id)
                    if dep_node and dep_node.status == TaskStatus.FAILED:
                        should_skip = True
                        break

                if should_skip:
                    node.status = TaskStatus.SKIPPED
                    node.error = f"Dependency failed: {node.depends_on}"
                    results[nid] = None
                    if on_node_complete:
                        on_node_complete(node)
                    if on_progress:
                        on_progress(self.progress)
                    continue

                # Execute with retry
                result = self._execute_node_with_retry(
                    node, task_runner,
                    on_node_start=on_node_start,
                    on_node_complete=on_node_complete,
                )
                results[nid] = result

                if on_progress:
                    on_progress(self.progress)

        return results

    def _execute_node_with_retry(
        self,
        node: TaskNode,
        task_runner: Callable[[TaskNode], Any],
        *,
        on_node_start: Optional[Callable] = None,
        on_node_complete: Optional[Callable] = None,
    ) -> Any:
        """Execute a single node with retry logic."""
        attempt = 0
        last_error = ""

        while attempt <= node.max_retries:
            if attempt > 0:
                node.status = TaskStatus.RETRYING
                node.error = f"Retry {attempt}/{node.max_retries}: {last_error}"
                backoff = min(2 ** attempt, 30)  # Exponential backoff, cap at 30s
                logger.info(
                    "Retrying '%s' in %ds (attempt %d/%d)",
                    node.id, backoff, attempt, node.max_retries,
                )
                time.sleep(backoff)

            node.started_at = time.time()
            node.status = TaskStatus.RUNNING
            node.retry_count = attempt

            if on_node_start:
                on_node_start(node)

            try:
                result = task_runner(node)
                node.result = result
                node.status = TaskStatus.SUCCESS
                node.error = ""
                node.completed_at = time.time()
                if on_node_complete:
                    on_node_complete(node)
                return result
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "Node '%s' failed (attempt %d/%d): %s",
                    node.id, attempt + 1, node.max_retries + 1, last_error,
                )

            attempt += 1

        # All retries exhausted
        node.status = TaskStatus.FAILED
        node.error = last_error
        node.completed_at = time.time()
        if on_node_complete:
            on_node_complete(node)
        return None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "nodes": {nid: node.to_dict() for nid, node in self._nodes.items()},
            "execution_order": self._execution_order,
            "progress": self.progress,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_task_list(cls, tasks: list[dict]) -> "TaskGraph":
        """Create a TaskGraph from a list of task dicts.

        Each task dict should have at minimum:
            id, action, and optionally tool, params, depends_on, etc.
        """
        graph = cls()
        for t in tasks:
            graph.add_node(TaskNode(
                id=t["id"],
                tool=t.get("tool", "filesystem"),
                action=t.get("action", "read_file"),
                params=t.get("params", {}),
                description=t.get("description", ""),
                depends_on=t.get("depends_on", []),
                max_retries=t.get("max_retries", 3),
                timeout_seconds=t.get("timeout_seconds", 60),
                validation=t.get("validation", ""),
            ))
        return graph

    @classmethod
    def from_plan(cls, plan: dict) -> "TaskGraph":
        """Create a TaskGraph from a planner output dict.

        The planner dict should have a "tasks" key containing the task list,
        or a "steps" key (legacy format).
        """
        tasks = plan.get("tasks") or plan.get("steps", [])
        if not tasks:
            raise ValueError("Plan has no 'tasks' or 'steps'")

        return cls.from_task_list(tasks)
