"""Mission Portfolio Management (Phase B).

Models multiple concurrent missions as a portfolio: each mission has a priority,
category, owner, optional group, dependencies on other missions, a budget, and a
deadline. The PortfolioManager tracks states, computes what is *runnable* now
(dependencies satisfied), and renders a portfolio view.

Pure and additive — this is a planning/tracking model, not an executor. It does
not run missions or touch the scheduler; Mission OS remains the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class MissionState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Mission:
    id: str
    title: str = ""
    priority: int = 5                       # lower = higher priority
    category: str = "general"               # feature | bugfix | infra | docs | …
    owner: str = ""                         # department / lead
    group: str = ""                         # mission group / program id
    depends_on: List[str] = field(default_factory=list)   # other mission ids
    budget_tokens: int = 0
    deadline: str = ""                      # ISO date string (opaque; not parsed)
    progress: float = 0.0                   # 0–100
    risk: str = "low"                       # low | medium | high
    state: MissionState = MissionState.QUEUED
    eta_min: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "priority": self.priority,
            "category": self.category, "owner": self.owner, "group": self.group,
            "depends_on": list(self.depends_on), "budget_tokens": self.budget_tokens,
            "deadline": self.deadline, "progress": round(self.progress, 1),
            "risk": self.risk, "state": self.state.value, "eta_min": round(self.eta_min, 1),
        }


_ACTIVE = (MissionState.QUEUED, MissionState.RUNNING, MissionState.BLOCKED, MissionState.PAUSED)
_DONE = (MissionState.COMPLETED, MissionState.FAILED, MissionState.CANCELLED)


class PortfolioManager:
    """Tracks concurrent missions, their dependencies, and portfolio health."""

    def __init__(self) -> None:
        self._missions: Dict[str, Mission] = {}

    # -- mutation -----------------------------------------------------------

    def add_mission(self, mission_id: str, *, title: str = "", priority: int = 5,
                    category: str = "general", owner: str = "", group: str = "",
                    depends_on=None, budget_tokens: int = 0, deadline: str = "") -> Mission:
        m = Mission(id=mission_id, title=title or mission_id, priority=priority,
                    category=category, owner=owner, group=group,
                    depends_on=list(depends_on or []), budget_tokens=budget_tokens,
                    deadline=deadline)
        self._missions[mission_id] = m
        return m

    def set_state(self, mission_id: str, state: MissionState) -> None:
        m = self._missions.get(mission_id)
        if m:
            m.state = state

    def update(self, mission_id: str, *, progress: Optional[float] = None,
               risk: Optional[str] = None, eta_min: Optional[float] = None) -> None:
        m = self._missions.get(mission_id)
        if not m:
            return
        if progress is not None:
            m.progress = max(0.0, min(100.0, progress))
            if m.progress >= 100.0 and m.state not in _DONE:
                m.state = MissionState.COMPLETED
        if risk is not None:
            m.risk = risk
        if eta_min is not None:
            m.eta_min = eta_min

    def get(self, mission_id: str) -> Optional[Mission]:
        return self._missions.get(mission_id)

    # -- queries ------------------------------------------------------------

    def _completed(self, mid: str) -> bool:
        m = self._missions.get(mid)
        return bool(m and m.state is MissionState.COMPLETED)

    def runnable(self) -> List[Mission]:
        """Queued missions whose dependencies are all completed, priority order."""
        out = [m for m in self._missions.values()
               if m.state is MissionState.QUEUED
               and all(self._completed(d) for d in m.depends_on)]
        return sorted(out, key=lambda m: (m.priority, m.id))

    def blocked_by_dependency(self) -> List[Mission]:
        return [m for m in self._missions.values()
                if m.state is MissionState.QUEUED
                and not all(self._completed(d) for d in m.depends_on)]

    def active(self) -> List[Mission]:
        return [m for m in self._missions.values() if m.state in _ACTIVE]

    def by_group(self, group: str) -> List[Mission]:
        return [m for m in self._missions.values() if m.group == group]

    def state_counts(self) -> dict:
        counts = {s.value: 0 for s in MissionState}
        for m in self._missions.values():
            counts[m.state.value] += 1
        return counts

    def health(self) -> dict:
        """Portfolio-level health summary for the operations center."""
        ms = list(self._missions.values())
        active = self.active()
        total_budget = sum(m.budget_tokens for m in ms)
        high_risk = sum(1 for m in active if m.risk == "high")
        avg_progress = round(sum(m.progress for m in ms) / len(ms), 1) if ms else 0.0
        risk = "high" if high_risk else ("medium" if any(m.risk == "medium" for m in active) else "low")
        return {
            "total": len(ms), "active": len(active),
            "running": sum(1 for m in active if m.state is MissionState.RUNNING),
            "blocked": sum(1 for m in active if m.state is MissionState.BLOCKED),
            "completed": sum(1 for m in ms if m.state is MissionState.COMPLETED),
            "avg_progress": avg_progress, "risk_level": risk,
            "high_risk_missions": high_risk, "total_budget_tokens": total_budget,
        }

    def snapshot(self) -> List[dict]:
        return [m.to_dict() for m in
                sorted(self._missions.values(), key=lambda m: (m.priority, m.id))]
