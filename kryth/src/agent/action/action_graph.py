"""Action Graph — a DAG of Actions derived from a mission.

The planner converts a user mission into an ActionGraph. Independent
actions within each layer are executed sequentially by the ActionScheduler.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from agent.action.action import Action, ActionRisk, ActionStatus

logger = logging.getLogger(__name__)


@dataclass
class ActionGraph:
    """Directed Acyclic Graph of Actions for one mission."""

    mission: str = ""
    actions: dict[str, Action] = field(default_factory=dict)

    # ── Construction ──────────────────────────────────────────────────

    def add(self, action: Action) -> Action:
        """Add an action; returns the action for chaining."""
        if action.id in self.actions:
            raise ValueError(f"Duplicate action id: {action.id}")
        self.actions[action.id] = action
        return action

    def add_dependency(self, action_id: str, depends_on_id: str) -> None:
        """Make action_id depend on depends_on_id."""
        if action_id not in self.actions:
            raise KeyError(action_id)
        if depends_on_id not in self.actions:
            raise KeyError(depends_on_id)
        if depends_on_id not in self.actions[action_id].dependencies:
            self.actions[action_id].dependencies.append(depends_on_id)

    # ── Topology ──────────────────────────────────────────────────────

    def ready(self, completed_ids: set[str], running_ids: set[str]) -> list[Action]:
        """Actions that are ready to start (all deps done, not yet started)."""
        result = []
        for a in self.actions.values():
            if a.status != ActionStatus.PENDING:
                continue
            if a.id in running_ids:
                continue
            if a.is_ready(completed_ids):
                result.append(a)
        result.sort(key=lambda x: x.priority)
        return result

    def layers(self) -> list[list[Action]]:
        """Topological layers — actions within each layer run sequentially."""
        completed: set[str] = set()
        layers: list[list[Action]] = []
        remaining = {a.id for a in self.actions.values()}
        while remaining:
            layer = [
                self.actions[aid]
                for aid in remaining
                if all(d in completed for d in self.actions[aid].dependencies)
            ]
            if not layer:
                raise ValueError(
                    f"Cycle detected in ActionGraph for mission '{self.mission}'"
                )
            layer.sort(key=lambda a: a.priority)
            layers.append(layer)
            for a in layer:
                completed.add(a.id)
            remaining -= {a.id for a in layer}
        return layers

    def validate(self) -> list[str]:
        """Return list of validation errors; empty = OK."""
        errors = []
        ids = set(self.actions.keys())
        for a in self.actions.values():
            for dep in a.dependencies:
                if dep not in ids:
                    errors.append(f"Action '{a.id}' depends on unknown '{dep}'")
        try:
            self.layers()
        except ValueError as e:
            errors.append(str(e))
        return errors

    # ── Status helpers ────────────────────────────────────────────────

    @property
    def is_complete(self) -> bool:
        return all(
            a.status in (ActionStatus.SUCCEEDED, ActionStatus.SKIPPED, ActionStatus.ROLLED_BACK)
            for a in self.actions.values()
        )

    @property
    def has_failures(self) -> bool:
        return any(a.status == ActionStatus.FAILED for a in self.actions.values())

    @property
    def progress(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in ActionStatus}
        for a in self.actions.values():
            counts[a.status.value] += 1
        counts["total"] = len(self.actions)
        return counts

    def iter_by_status(self, status: ActionStatus) -> Iterator[Action]:
        return (a for a in self.actions.values() if a.status == status)

    # ── Serialisation ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission": self.mission,
            "progress": self.progress,
            "actions": [a.to_dict() for a in self.actions.values()],
        }

    # ── Factory ───────────────────────────────────────────────────────

    @classmethod
    def from_plan(cls, plan: dict[str, Any]) -> "ActionGraph":
        """Build an ActionGraph from a planner-produced dict.

        Expected format::

            {
              "mission": "Deploy SaaS",
              "actions": [
                {
                  "id": "build",
                  "goal": "Compile the backend",
                  "risk": "low",
                  "priority": 10,
                  "dependencies": [],
                  "preferred_executors": ["terminal"],
                  "verification_steps": ["check exit code 0"],
                  "rollback_steps": ["remove build artefacts"],
                  "params": {"command": "make build"}
                },
                ...
              ]
            }
        """
        graph = cls(mission=plan.get("mission", ""))
        for item in plan.get("actions", []):
            risk_raw = item.get("risk", "low").lower()
            try:
                risk = ActionRisk(risk_raw)
            except ValueError:
                risk = ActionRisk.LOW

            action = Action(
                id=item["id"],
                goal=item.get("goal", item.get("description", item["id"])),
                description=item.get("description", ""),
                priority=int(item.get("priority", 50)),
                dependencies=list(item.get("dependencies", [])),
                risk=risk,
                estimated_cost_tokens=int(item.get("estimated_cost_tokens", 0)),
                estimated_time_ms=float(item.get("estimated_time_ms", 0)),
                requirements=list(item.get("requirements", [])),
                preferred_executors=list(item.get("preferred_executors", [])),
                verification_steps=list(item.get("verification_steps", [])),
                rollback_steps=list(item.get("rollback_steps", [])),
                affected_paths=list(item.get("affected_paths", [])),
                confidence=float(item.get("confidence", 1.0)),
                params=dict(item.get("params", {})),
            )
            graph.add(action)
        return graph
