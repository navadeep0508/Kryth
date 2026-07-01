"""Mission Allocator — decides whether a mission can start given resource constraints."""

from __future__ import annotations

import logging
from typing import Optional

from agent.mos.mission_state import Mission, MissionStatus, ResourceBudget
from agent.mos.resource_manager import ResourceManager

logger = logging.getLogger(__name__)


class MissionAllocator:
    """Gate that decides whether the MOS can start a mission right now.

    Checks:
    1. Dependency readiness (delegated — caller must check MissionDependencies)
    2. Resource availability via ResourceManager
    3. Mission-level pre-conditions (valid goal, not terminal)
    """

    def __init__(self, resource_manager: ResourceManager) -> None:
        self._resources = resource_manager

    def can_start(self, mission: Mission) -> tuple[bool, str]:
        """Return (ok, reason). reason is '' if ok."""
        if mission.is_terminal():
            return False, f"mission already terminal ({mission.status.value})"

        if mission.status == MissionStatus.RUNNING:
            return False, "mission already running"

        if not mission.goal.strip():
            return False, "mission has no goal"

        ok, reason = self._resources.can_admit(mission.budget)
        if not ok:
            return False, f"resource constraint: {reason}"

        return True, ""

    def admit(self, mission: Mission) -> bool:
        """Allocate resources and mark mission as running. Returns True on success."""
        ok, reason = self.can_start(mission)
        if not ok:
            logger.warning("Cannot admit mission %s: %s", mission.id, reason)
            return False

        self._resources.allocate(mission.id, mission.budget)
        from datetime import datetime, timezone
        mission.status     = MissionStatus.RUNNING
        mission.started_at = datetime.now(timezone.utc).isoformat()
        logger.info("Admitted mission %s: %s", mission.id, mission.name)
        return True

    def release(self, mission: Mission) -> None:
        """Free all resources held by the mission."""
        self._resources.release(mission.id)
        logger.debug("Released resources for mission %s", mission.id)

    def build_default_budget(
        self,
        max_workers: int = 4,
        max_tokens: int  = 200_000,
        max_api_calls: int = 500,
    ) -> ResourceBudget:
        return ResourceBudget(
            max_workers=max_workers,
            max_tokens=max_tokens,
            max_api_calls=max_api_calls,
        )
