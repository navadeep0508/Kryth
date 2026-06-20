"""Shared work queue for dynamic agent scheduling.

Supports priority ordering, work stealing (idle agents claim pending tasks),
and automatic retry on failure (up to MAX_RETRIES).

Usage in scheduler:
    queue = WorkQueue()
    queue.submit("task-id", description, prompt, max_turns, priority=5)

    # In each worker thread:
    item = queue.try_steal()
    if item:
        queue.mark_running(item.task_id)
        try:
            result = run_agent(item.prompt, item.max_turns)
            queue.complete(item.task_id, result)
        except Exception as e:
            queue.fail(item.task_id, str(e))
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class TaskStatus(Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"


MAX_RETRIES = 2


@dataclass
class WorkItem:
    task_id: str
    description: str
    prompt: str
    max_turns: int
    priority: int = 5        # lower = higher priority
    retry_count: int = 0
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    error: str = ""

    def __lt__(self, other: "WorkItem") -> bool:
        return self.priority < other.priority


class WorkQueue:
    """Thread-safe priority work queue with work stealing and retry.

    Priority ordering: items with lower priority numbers run first.
    Work stealing: idle agents call try_steal() to pick up pending tasks
    from the global pool after finishing their assigned work.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: Dict[str, WorkItem] = {}
        self._pending: List[str] = []   # task_ids ordered by priority

    def submit(
        self,
        task_id: str,
        description: str,
        prompt: str,
        max_turns: int,
        priority: int = 5,
    ) -> None:
        """Add a new task to the queue."""
        with self._lock:
            if task_id in self._items:
                return  # idempotent
            item = WorkItem(
                task_id=task_id,
                description=description,
                prompt=prompt,
                max_turns=max_turns,
                priority=priority,
            )
            self._items[task_id] = item
            self._pending.append(task_id)
            self._pending.sort(key=lambda tid: self._items[tid].priority)

    def try_steal(self) -> Optional[WorkItem]:
        """Atomically claim the highest-priority pending task.

        Returns the WorkItem (now marked RUNNING) or None if the queue
        is empty. Callers must call complete() or fail() when done.
        """
        with self._lock:
            for tid in list(self._pending):
                item = self._items.get(tid)
                if item and item.status == TaskStatus.PENDING:
                    item.status = TaskStatus.RUNNING
                    self._pending.remove(tid)
                    return item
        return None

    def mark_running(self, task_id: str) -> None:
        with self._lock:
            item = self._items.get(task_id)
            if item:
                item.status = TaskStatus.RUNNING
                if task_id in self._pending:
                    self._pending.remove(task_id)

    def complete(self, task_id: str, result: str) -> None:
        with self._lock:
            item = self._items.get(task_id)
            if item:
                item.status = TaskStatus.DONE
                item.result = result

    def fail(self, task_id: str, error: str, retry: bool = True) -> None:
        """Mark a task failed. Re-enqueues if retry is True and retries remain."""
        with self._lock:
            item = self._items.get(task_id)
            if not item:
                return
            item.retry_count += 1
            item.error = error
            if retry and item.retry_count <= MAX_RETRIES:
                item.status = TaskStatus.PENDING
                item.priority = max(0, item.priority - 1)  # bump priority on retry
                # Only re-queue if currently not already pending (avoid duplicates)
                if task_id not in self._pending:
                    self._pending.append(task_id)
                    self._pending.sort(key=lambda tid: self._items[tid].priority)
            else:
                item.status = TaskStatus.FAILED

    def status(self) -> dict:
        with self._lock:
            counts = {s.value: 0 for s in TaskStatus}
            for item in self._items.values():
                counts[item.status.value] += 1
            return counts

    def is_empty(self) -> bool:
        with self._lock:
            return not self._pending

    def all_done(self) -> bool:
        """True when every task is DONE or FAILED (nothing pending/running)."""
        with self._lock:
            return all(
                item.status in (TaskStatus.DONE, TaskStatus.FAILED)
                for item in self._items.values()
            )

    def results(self) -> Dict[str, str]:
        """Return {task_id: result} for all completed tasks."""
        with self._lock:
            return {
                tid: item.result
                for tid, item in self._items.items()
                if item.status == TaskStatus.DONE
            }

    def failed_tasks(self) -> List[str]:
        with self._lock:
            return [
                tid for tid, item in self._items.items()
                if item.status == TaskStatus.FAILED
            ]
