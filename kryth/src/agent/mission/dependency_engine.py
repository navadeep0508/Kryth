"""DependencyEngine — propagate change signals across dependent entities.

Uses ``agent.repo_index.lookup_dependents()`` to find which files import
or call a changed file, then marks those as requiring verification in the
mission DAG.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.mission.artifact_tracker import ArtifactTracker
    from agent.mission.mission_dag import MissionDAG

logger = logging.getLogger(__name__)


@dataclass
class ImpactResult:
    changed_path: str
    affected_symbols: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    dependent_task_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "changed_path": self.changed_path,
            "affected_symbols": self.affected_symbols,
            "affected_files": self.affected_files,
            "affected_tests": self.affected_tests,
            "dependent_task_ids": self.dependent_task_ids,
        }


class DependencyEngine:
    """Compute and propagate change impact for mission-aware file changes.

    Parameters
    ----------
    artifact_tracker:
        ArtifactTracker instance for the project (used to find task ownership).
    mission_dag:
        MissionDAG instance (used to reset dirty dependent tasks).
    """

    def __init__(
        self,
        artifact_tracker: "ArtifactTracker | None" = None,
        mission_dag: "MissionDAG | None" = None,
    ) -> None:
        self._tracker = artifact_tracker
        self._dag = mission_dag

    # ------------------------------------------------------------------
    # Impact computation
    # ------------------------------------------------------------------

    def compute_impact(self, changed_path: str, root: str = ".") -> ImpactResult:
        """Return all entities and tasks affected by a change to *changed_path*."""
        result = ImpactResult(changed_path=changed_path)

        # Get dependents from the repo index
        try:
            import agent.repo_index as ri
            deps = ri.lookup_dependents(Path(changed_path).stem, root)
            result.affected_files = list(set(
                deps.get("imports", []) + deps.get("calls", [])
            ))
            result.affected_symbols = list(deps.get("imports", []))
        except Exception as exc:
            logger.debug("DependencyEngine.compute_impact repo_index error: %s", exc)

        # Identify test files among affected files
        result.affected_tests = [f for f in result.affected_files if "test" in f.lower()]

        # Find which tasks produced the changed file
        if self._tracker:
            history = self._tracker.get_by_path(changed_path)
            task_ids = list({h["task_node_id"] for h in history if h["task_node_id"]})
            result.dependent_task_ids = task_ids

        return result

    def mark_dependent_tasks_dirty(
        self,
        mission_id: str,
        changed_path: str,
        root: str = ".",
    ) -> list[str]:
        """Reset dependent task nodes to PENDING so they re-run.

        Returns the list of task node IDs that were reset.
        """
        if self._dag is None or self._tracker is None:
            return []

        # Find tasks in this mission that produced/modified files that import changed_path
        impact = self.compute_impact(changed_path, root)
        reset_ids: list[str] = []

        for affected_file in impact.affected_files:
            arts = self._tracker.get_by_path(affected_file)
            for art in arts:
                if art["mission_id"] == mission_id and art["task_node_id"]:
                    node = self._dag.get_node(mission_id, art["task_node_id"])
                    if node and node["status"] == "completed":
                        self._dag.update_node(mission_id, art["task_node_id"], status="pending")
                        reset_ids.append(art["task_node_id"])
                        logger.info(
                            "Task %s marked dirty due to change in %s",
                            art["task_node_id"], changed_path,
                        )

        return list(set(reset_ids))

    def build_dependency_graph(self, root: str = ".") -> dict[str, list[str]]:
        """Return a dict mapping each Python file to the files that depend on it."""
        try:
            import agent.repo_index as ri
            ri.build_index(root)
            graph: dict[str, list[str]] = {}
            for name in list(ri._index.keys()):
                deps = ri.lookup_dependents(name, root)
                all_deps = list(set(deps.get("imports", []) + deps.get("calls", [])))
                if all_deps:
                    graph[name] = all_deps
            return graph
        except Exception as exc:
            logger.warning("build_dependency_graph failed: %s", exc)
            return {}
