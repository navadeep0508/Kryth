"""Factory — top-level façade for the Autonomous Software Factory.

Composes all subsystems into a single entry point.  Every method is a
thin coordinator — all heavy logic lives in the individual modules.

Reuses (never duplicates):
- MissionManager — DAG persistence and task tracking
- BacklogManager — epic/story/task hierarchy
- ProductPlanner — goal → epics
- SprintEngine — sprint lifecycle
- ArchitectureGuardian — structural verification
- CodeReviewEngine — pre-merge review
- TestingPipeline — automated testing
- BrowserQA — browser validation
- DeploymentPipeline — build/deploy/rollback
- MaintenanceEngine — post-completion monitoring
- ExecutiveDashboard — progress display
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Factory:
    """Autonomous Software Factory.

    Parameters
    ----------
    project_hash:
        12-char project identity from ``persistence.project_hash_for()``.
    project_name:
        Human-readable project name.
    root:
        Project root directory.
    memory_manager:
        Optional MemoryManager for memory integration.
    db_path:
        Override for all SQLite databases (for tests).
    """

    def __init__(
        self,
        project_hash: str = "",
        project_name: str = "KRYTH Project",
        root: str = ".",
        memory_manager: Any = None,
        db_path: Optional[Path] = None,
    ) -> None:
        if not project_hash:
            try:
                from agent.persistence import project_hash_for
                project_hash = project_hash_for(root)
            except Exception:
                project_hash = "default"

        self.project_hash = project_hash
        self.project_name = project_name
        self.root = root
        self._mm_ext = memory_manager
        self._db_path = db_path

        # All subsystems — lazy via properties
        self._mission: Any = None
        self._backlog: Any = None
        self._planner: Any = None
        self._sprint: Any = None
        self._guardian: Any = None
        self._review: Any = None
        self._testing: Any = None
        self._browser_qa: Any = None
        self._deployment: Any = None
        self._maintenance: Any = None
        self._dashboard: Any = None

    # ------------------------------------------------------------------
    # Subsystem properties (lazy)
    # ------------------------------------------------------------------

    @property
    def mission(self):
        if self._mission is None:
            from agent.mission.mission_manager import MissionManager
            self._mission = MissionManager(
                project_hash=self.project_hash,
                cwd=self.root,
                memory_manager=self._mm_ext,
                db_path=self._db_path,
            )
        return self._mission

    @property
    def backlog(self):
        if self._backlog is None:
            from agent.factory.backlog_manager import BacklogManager
            db = self._db_path.parent / f"backlog_{self.project_hash}.db" if self._db_path else None
            self._backlog = BacklogManager(self.project_hash, db_path=db)
        return self._backlog

    @property
    def planner(self):
        if self._planner is None:
            from agent.factory.product_planner import ProductPlanner
            self._planner = ProductPlanner(self.project_hash, backlog=self.backlog)
        return self._planner

    @property
    def sprint(self):
        if self._sprint is None:
            from agent.factory.sprint_engine import SprintEngine
            db = self._db_path.parent / f"sprint_{self.project_hash}.db" if self._db_path else None
            self._sprint = SprintEngine(
                self.project_hash,
                mission_manager=self.mission,
                backlog=self.backlog,
                db_path=db,
            )
        return self._sprint

    @property
    def guardian(self):
        if self._guardian is None:
            from agent.factory.architecture_guardian import ArchitectureGuardian
            self._guardian = ArchitectureGuardian(self.root)
        return self._guardian

    @property
    def review(self):
        if self._review is None:
            from agent.factory.code_review_engine import CodeReviewEngine
            self._review = CodeReviewEngine(self.root)
        return self._review

    @property
    def testing(self):
        if self._testing is None:
            from agent.factory.testing_pipeline import TestingPipeline
            self._testing = TestingPipeline(self.root)
        return self._testing

    @property
    def browser_qa(self):
        if self._browser_qa is None:
            from agent.factory.browser_qa import BrowserQA
            self._browser_qa = BrowserQA()
        return self._browser_qa

    @property
    def deployment(self):
        if self._deployment is None:
            from agent.factory.deployment_pipeline import DeploymentPipeline
            db = self._db_path.parent / f"deploy_{self.project_hash}.db" if self._db_path else None
            self._deployment = DeploymentPipeline(
                self.project_hash, root=self.root,
                mission_manager=self.mission, db_path=db,
            )
        return self._deployment

    @property
    def maintenance(self):
        if self._maintenance is None:
            from agent.factory.maintenance_engine import MaintenanceEngine
            self._maintenance = MaintenanceEngine(self.root, self.project_hash)
        return self._maintenance

    @property
    def dashboard(self):
        if self._dashboard is None:
            from agent.factory.executive_dashboard import ExecutiveDashboard
            self._dashboard = ExecutiveDashboard(
                project_name=self.project_name,
                mission_manager=self.mission,
                backlog=self.backlog,
                sprint_engine=self.sprint,
                deployment=self.deployment,
            )
        return self._dashboard

    # ------------------------------------------------------------------
    # High-level factory API
    # ------------------------------------------------------------------

    def build(self, goal: str) -> dict:
        """Start a full product build from a high-level goal.

        Returns a dict with mission_id, plan summary, and sprint_id.
        """
        # 1. Plan
        plan = self.planner.plan(goal, project_name=self.project_name, project_root=self.root)

        # 2. Create mission
        mission_id = self.mission.start(
            name=f"Build: {plan.project_name}",
            goal=goal,
        )

        # 3. Create sprint from first epic's stories
        first_story_ids: list[str] = []
        for epic_title, epic_id in plan.backlog_ids.items():
            stories = self.backlog.list_stories(epic_id=epic_id)
            first_story_ids = [s["id"] for s in stories[:5]]
            break

        sprint_id = self.sprint.create_sprint(
            name=f"Sprint 1 — {plan.project_name}",
            goal=goal,
            story_ids=first_story_ids,
        )

        return {
            "mission_id": mission_id,
            "sprint_id": sprint_id,
            "project_name": plan.project_name,
            "epics": len(plan.epics),
            "tech_stack": plan.tech_stack,
            "risk_notes": plan.risk_notes,
        }

    def status(self, mission_id: str = "") -> dict:
        """Return a combined factory status: mission + backlog + sprint."""
        result: dict = {"project": self.project_name}
        if mission_id:
            result["mission"] = self.mission.status(mission_id)
        result["backlog"] = self.backlog.backlog_summary()
        active_sprints = self.sprint.list_sprints(status="active")
        result["active_sprints"] = len(active_sprints)
        return result

    def dashboard_render(self, mission_id: str = "") -> str:
        return self.dashboard.render(mission_id=mission_id)
