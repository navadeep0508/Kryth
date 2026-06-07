"""ExecutiveDashboard — render a live Markdown/text mission progress overview.

Aggregates data from MissionManager, BacklogManager, SprintEngine,
DeploymentPipeline, and ExecutionSupervisor without duplicating any storage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DashboardState:
    project_name: str
    progress_percent: float = 0.0
    active_sprint: str = ""
    teams: list[dict] = field(default_factory=list)
    risk_level: str = "LOW"
    success_prediction: float = 95.0
    epics_done: int = 0
    epics_total: int = 0
    stories_done: int = 0
    stories_total: int = 0
    last_deployment: str = ""
    active_issues: int = 0
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "progress_percent": self.progress_percent,
            "active_sprint": self.active_sprint,
            "teams": self.teams,
            "risk_level": self.risk_level,
            "success_prediction": self.success_prediction,
            "epics_done": self.epics_done,
            "epics_total": self.epics_total,
            "stories_done": self.stories_done,
            "stories_total": self.stories_total,
            "last_deployment": self.last_deployment,
            "active_issues": self.active_issues,
        }


class ExecutiveDashboard:
    """Build and render the executive project dashboard.

    Parameters
    ----------
    project_name:
        Human-readable project name.
    mission_manager:
        MissionManager instance.
    backlog:
        BacklogManager instance.
    sprint_engine:
        SprintEngine instance.
    deployment:
        DeploymentPipeline instance.
    supervisor:
        ExecutionSupervisor instance.
    """

    def __init__(
        self,
        project_name: str = "KRYTH Project",
        mission_manager: Any = None,
        backlog: Any = None,
        sprint_engine: Any = None,
        deployment: Any = None,
        supervisor: Any = None,
    ) -> None:
        self.project_name = project_name
        self._mm = mission_manager
        self._backlog = backlog
        self._sprint = sprint_engine
        self._deployment = deployment
        self._supervisor = supervisor

    def state(self, mission_id: str = "") -> DashboardState:
        """Compute and return the current dashboard state."""
        ds = DashboardState(project_name=self.project_name)

        # Mission progress
        if self._mm and mission_id:
            try:
                prog = self._mm.dag.progress(mission_id)
                ds.progress_percent = prog.get("percent", 0)
            except Exception:
                pass

        # Backlog
        if self._backlog:
            try:
                summary = self._backlog.backlog_summary()
                story_counts = summary.get("stories", {})
                ds.stories_done = story_counts.get("done", 0)
                ds.stories_total = sum(story_counts.values())
                epic_counts = summary.get("epics", 0)
                ds.epics_total = epic_counts
            except Exception:
                pass

        # Active sprint
        if self._sprint:
            try:
                active = self._sprint.list_sprints(status="active")
                if active:
                    ds.active_sprint = active[0]["name"]
            except Exception:
                pass

        # Deployment
        if self._deployment:
            try:
                deps = self._deployment.list_deployments()
                if deps:
                    ds.last_deployment = f"{deps[0]['env']} ({deps[0]['status']})"
            except Exception:
                pass

        # Supervisor health
        if self._supervisor:
            try:
                health = self._supervisor.monitor()
                if isinstance(health, dict):
                    risk = health.get("risk_level", "low")
                    ds.risk_level = risk.upper()
            except Exception:
                pass

        # Success prediction (heuristic)
        ds.success_prediction = self._predict_success(ds)

        return ds

    def render(self, mission_id: str = "") -> str:
        """Return a Markdown executive dashboard string."""
        ds = self.state(mission_id=mission_id)
        return self._render_markdown(ds)

    def render_teams(self, agents: list[dict]) -> str:
        """Render team status for a given list of agent role dicts."""
        lines = ["## Teams\n"]
        for agent in agents:
            name = agent.get("role", agent.get("name", "Agent"))
            status = agent.get("status", "pending")
            icon = {"completed": "✓", "active": "◉", "pending": "○", "failed": "✗"}.get(status, "○")
            lines.append(f"{icon} {name}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _render_markdown(self, ds: DashboardState) -> str:
        pct = ds.progress_percent
        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        lines = [
            f"# {ds.project_name}",
            f"\n{bar} **{pct:.0f}%**\n",
        ]

        if ds.active_sprint:
            lines.append(f"**Sprint:** {ds.active_sprint}")

        if ds.epics_total > 0:
            lines.append(f"**Epics:** {ds.epics_done}/{ds.epics_total} done")
        if ds.stories_total > 0:
            lines.append(f"**Stories:** {ds.stories_done}/{ds.stories_total} done")

        if ds.last_deployment:
            lines.append(f"**Last Deploy:** {ds.last_deployment}")

        risk_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(ds.risk_level, "⚪")
        lines.append(f"\n**Risk:** {risk_icon} {ds.risk_level}")
        lines.append(f"**Success Prediction:** {ds.success_prediction:.0f}%")

        if ds.active_issues > 0:
            lines.append(f"**Active Issues:** {ds.active_issues}")

        return "\n".join(lines)

    @staticmethod
    def _predict_success(ds: DashboardState) -> float:
        base = 90.0
        if ds.progress_percent > 80:
            base += 5
        if ds.risk_level == "HIGH":
            base -= 20
        elif ds.risk_level == "MEDIUM":
            base -= 10
        if ds.active_issues > 5:
            base -= 5
        return max(10.0, min(99.0, base))
