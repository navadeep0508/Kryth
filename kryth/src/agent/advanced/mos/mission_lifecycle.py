"""Mission Lifecycle — state-machine transitions for a Mission."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from agent.mos.mission_state import Mission, MissionStatus

logger = logging.getLogger(__name__)

# Valid transitions: from_status → set of allowed to_statuses
_TRANSITIONS: dict[MissionStatus, set[MissionStatus]] = {
    MissionStatus.QUEUED:    {MissionStatus.RUNNING, MissionStatus.BLOCKED, MissionStatus.FAILED, MissionStatus.ARCHIVED},
    MissionStatus.RUNNING:   {MissionStatus.PAUSED, MissionStatus.BLOCKED, MissionStatus.COMPLETED, MissionStatus.FAILED},
    MissionStatus.PAUSED:    {MissionStatus.RUNNING, MissionStatus.FAILED, MissionStatus.ARCHIVED},
    MissionStatus.BLOCKED:   {MissionStatus.QUEUED, MissionStatus.FAILED, MissionStatus.ARCHIVED},
    MissionStatus.COMPLETED: {MissionStatus.ARCHIVED},
    MissionStatus.FAILED:    {MissionStatus.QUEUED, MissionStatus.ARCHIVED},  # retry supported
    MissionStatus.ARCHIVED:  set(),
}


class MissionLifecycle:
    """Applies and validates Mission state transitions."""

    def transition(
        self,
        mission: Mission,
        to: MissionStatus,
        reason: str = "",
    ) -> bool:
        """Attempt a transition. Returns True if successful."""
        allowed = _TRANSITIONS.get(mission.status, set())
        if to not in allowed:
            logger.warning(
                "Invalid transition %s → %s for mission %s",
                mission.status.value, to.value, mission.id,
            )
            return False

        now = datetime.now(timezone.utc).isoformat()
        prev = mission.status
        mission.status        = to
        mission.status_reason = reason

        if to == MissionStatus.RUNNING and not mission.started_at:
            mission.started_at = now
        elif to == MissionStatus.PAUSED:
            mission.paused_at = now
        elif to in (MissionStatus.COMPLETED, MissionStatus.FAILED):
            mission.completed_at = now

        logger.info(
            "Mission %s: %s → %s%s",
            mission.id, prev.value, to.value,
            f" ({reason})" if reason else "",
        )
        return True

    def pause(self, mission: Mission, reason: str = "") -> bool:
        return self.transition(mission, MissionStatus.PAUSED, reason)

    def resume(self, mission: Mission) -> bool:
        return self.transition(mission, MissionStatus.RUNNING, "resumed")

    def block(self, mission: Mission, reason: str = "") -> bool:
        return self.transition(mission, MissionStatus.BLOCKED, reason)

    def unblock(self, mission: Mission) -> bool:
        return self.transition(mission, MissionStatus.QUEUED, "dependency satisfied")

    def complete(self, mission: Mission, summary: str = "") -> bool:
        mission.result_summary = summary
        return self.transition(mission, MissionStatus.COMPLETED, "success")

    def fail(self, mission: Mission, reason: str = "") -> bool:
        return self.transition(mission, MissionStatus.FAILED, reason)

    def archive(self, mission: Mission) -> bool:
        return self.transition(mission, MissionStatus.ARCHIVED, "archived")

    def retry(self, mission: Mission) -> bool:
        """Re-queue a failed mission (reset failure state)."""
        if mission.status != MissionStatus.FAILED:
            logger.warning("Cannot retry mission %s: not in FAILED state", mission.id)
            return False
        mission.status_reason  = "retrying"
        mission.started_at     = None
        mission.completed_at   = None
        mission.result_summary = ""
        return self.transition(mission, MissionStatus.QUEUED, "retry requested")

    @staticmethod
    def can_transition(from_status: MissionStatus, to_status: MissionStatus) -> bool:
        return to_status in _TRANSITIONS.get(from_status, set())
