"""Dynamic Replanner — rebuild execution plan when runtime changes.

Triggered when:
  - A critical agent crashes mid-execution
  - A terminal step fails with no recovery path
  - The task DAG becomes unreachable due to dependency failure
  - The mission goal changes (user interrupts with new context)

Replan strategy:
  1. Identify which work is incomplete
  2. Reassign crashed agent's tasks to available agents
  3. Rebuild the minimal DAG for remaining work
  4. Resume from the last successful checkpoint
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ReplanEvent:
    reason: str
    failed_agent: Optional[str] = None
    failed_step: Optional[str] = None
    remaining_tasks: List[str] = field(default_factory=list)
    available_agents: List[str] = field(default_factory=list)


@dataclass
class ReplanResult:
    success: bool
    strategy: str                  # reassign | rebuild | abort | resume
    new_assignments: Dict[str, str]  # task_id → agent_id
    skip_tasks: List[str]
    resume_from: Optional[str]
    reason: str


class DynamicReplanner:
    """Rebuild execution plans in response to runtime failures."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._replan_count: int = 0
        self._history: List[ReplanResult] = []

    def replan(
        self,
        event: ReplanEvent,
        project_root: str = ".",
    ) -> ReplanResult:
        """Build a new execution plan in response to a runtime event."""
        with self._lock:
            self._replan_count += 1

        result = self._choose_strategy(event, project_root)

        with self._lock:
            self._history.append(result)

        # Emit timeline event
        try:
            from agent import ui
            ui.timeline_event(
                f"Replanning: {result.reason} ({result.strategy})", "warn"
            )
        except Exception:
            pass

        return result

    def _choose_strategy(
        self, event: ReplanEvent, project_root: str
    ) -> ReplanResult:
        available = list(event.available_agents)
        remaining = list(event.remaining_tasks)

        # No remaining tasks → mission can complete without replan
        if not remaining:
            return ReplanResult(
                success=True,
                strategy="resume",
                new_assignments={},
                skip_tasks=[],
                resume_from=None,
                reason="No remaining tasks — mission can complete",
            )

        # No available agents → abort or try to spawn new one
        if not available:
            return ReplanResult(
                success=False,
                strategy="abort",
                new_assignments={},
                skip_tasks=remaining,
                resume_from=None,
                reason="No available agents to reassign work to",
            )

        # Attempt reassignment: distribute remaining tasks to available agents
        assignments: Dict[str, str] = {}
        for i, task in enumerate(remaining):
            agent = available[i % len(available)]
            assignments[task] = agent

        # Check if experience suggests a different team
        try:
            from agent.experience import get_experience
            exp = get_experience(project_root)
            team_rec = exp.recommend("team", event.reason)
            if team_rec and hasattr(team_rec, "roles") and team_rec.confidence > 0.70:
                reason = (
                    f"Reassigned {len(remaining)} tasks to {len(available)} available agents "
                    f"(experience-guided: {team_rec.strategy})"
                )
            else:
                reason = (
                    f"Reassigned {len(remaining)} tasks to {len(available)} available agents"
                )
        except Exception:
            reason = f"Reassigned {len(remaining)} tasks to {len(available)} agents"

        return ReplanResult(
            success=True,
            strategy="reassign",
            new_assignments=assignments,
            skip_tasks=[],
            resume_from=event.failed_step,
            reason=reason,
        )

    @property
    def replan_count(self) -> int:
        with self._lock:
            return self._replan_count

    def get_history(self) -> List[ReplanResult]:
        with self._lock:
            return list(self._history)


# Module-level singleton
dynamic_replanner = DynamicReplanner()
