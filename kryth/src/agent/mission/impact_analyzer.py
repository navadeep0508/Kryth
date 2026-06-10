"""ImpactAnalyzer — report change impact and schedule verification tasks.

Wraps DependencyEngine for the LLM tool layer. Called after file writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.mission.dependency_engine import DependencyEngine
    from agent.mission.mission_dag import MissionDAG

logger = logging.getLogger(__name__)


@dataclass
class ImpactReport:
    changed_paths: list[str]
    affected_modules: list[str] = field(default_factory=list)
    affected_apis: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    dirty_task_ids: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "changed_paths": self.changed_paths,
            "affected_modules": self.affected_modules,
            "affected_apis": self.affected_apis,
            "affected_tests": self.affected_tests,
            "dirty_task_ids": self.dirty_task_ids,
            "recommended_actions": self.recommended_actions,
        }

    def summary(self) -> str:
        lines = [f"Changed: {', '.join(self.changed_paths)}"]
        if self.affected_modules:
            lines.append(f"Affected modules: {', '.join(self.affected_modules[:10])}")
        if self.affected_tests:
            lines.append(f"Tests to re-run: {', '.join(self.affected_tests[:5])}")
        if self.dirty_task_ids:
            lines.append(f"Tasks reset to pending: {', '.join(self.dirty_task_ids)}")
        if self.recommended_actions:
            for a in self.recommended_actions:
                lines.append(f"→ {a}")
        return "\n".join(lines)


class ImpactAnalyzer:
    """Analyze the downstream impact of file changes.

    Parameters
    ----------
    dep_engine:
        DependencyEngine instance.
    mission_dag:
        MissionDAG for scheduling verification nodes.
    """

    def __init__(
        self,
        dep_engine: "DependencyEngine | None" = None,
        mission_dag: "MissionDAG | None" = None,
    ) -> None:
        self._deps = dep_engine
        self._dag = mission_dag

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze(
        self,
        changed_paths: list[str],
        root: str = ".",
        mission_id: str | None = None,
    ) -> ImpactReport:
        """Compute the impact of changes to *changed_paths*.

        Optionally resets dependent task nodes to PENDING in *mission_id*.
        """
        report = ImpactReport(changed_paths=changed_paths)

        for path in changed_paths:
            if self._deps is None:
                continue
            impact = self._deps.compute_impact(path, root)
            report.affected_modules.extend(impact.affected_files)
            report.affected_tests.extend(impact.affected_tests)

            if mission_id:
                dirty = self._deps.mark_dependent_tasks_dirty(mission_id, path, root)
                report.dirty_task_ids.extend(dirty)

        # Deduplicate
        report.affected_modules = list(dict.fromkeys(report.affected_modules))
        report.affected_tests = list(dict.fromkeys(report.affected_tests))
        report.dirty_task_ids = list(dict.fromkeys(report.dirty_task_ids))

        # Detect API files
        report.affected_apis = [
            f for f in report.affected_modules
            if any(kw in f.lower() for kw in ("route", "api", "view", "endpoint", "handler"))
        ]

        # Recommended actions
        if report.affected_tests:
            report.recommended_actions.append(f"Re-run {len(report.affected_tests)} test file(s)")
        if report.affected_apis:
            report.recommended_actions.append("Verify API contracts haven't broken")
        if report.dirty_task_ids:
            report.recommended_actions.append(f"Resume {len(report.dirty_task_ids)} dirty task(s)")

        return report

    def schedule_verification(
        self,
        impact_report: ImpactReport,
        mission_id: str,
    ) -> list[str]:
        """Add verification task nodes to the mission DAG for dirty tests.

        Returns list of added node IDs.
        """
        if self._dag is None:
            return []
        added: list[str] = []
        for test_file in impact_report.affected_tests[:5]:  # cap at 5 auto-verifications
            node_id = f"verify-{test_file.replace('/', '-').replace('.', '-')}"
            self._dag.add_node(mission_id, {
                "id": node_id,
                "tool": "shell",
                "action": "run_command",
                "params": {"command": f"python -m pytest {test_file} -x -q"},
                "description": f"Auto-verify {test_file} after dependency change",
                "depends_on": impact_report.dirty_task_ids,
                "status": "pending",
            })
            added.append(node_id)
        return added
