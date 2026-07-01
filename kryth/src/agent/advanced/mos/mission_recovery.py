"""Mission Recovery — restores in-progress missions after a crash or restart."""

from __future__ import annotations

import logging
from typing import Optional

from agent.mos.mission_state import Mission, MissionStatus
from agent.mos.mission_snapshot import MissionSnapshot
from agent.mos.mission_lifecycle import MissionLifecycle

logger = logging.getLogger(__name__)


class MissionRecovery:
    """Loads mission snapshots on startup and re-queues interruptible missions.

    Recovery policy per status:
    - RUNNING  → revert to QUEUED (was interrupted mid-flight)
    - PAUSED   → restore as PAUSED (user intentionally paused)
    - BLOCKED  → restore as BLOCKED (dependencies still apply)
    - QUEUED   → restore as QUEUED (never started)
    - COMPLETED / FAILED / ARCHIVED → keep as-is (terminal, no action)
    """

    def __init__(self, snapshot: MissionSnapshot) -> None:
        self._snap = snapshot
        self._lifecycle = MissionLifecycle()

    def recover(self) -> list[Mission]:
        """Load all snapshots and return missions ready to restore to portfolio."""
        snapshots = self._snap.load_all()
        if not snapshots:
            return []

        logger.info("Recovery: found %d mission snapshots", len(snapshots))
        recovered: list[Mission] = []

        for m in snapshots:
            restored = self._restore(m)
            if restored:
                recovered.append(restored)

        logger.info("Recovery: restored %d missions", len(recovered))
        return recovered

    def _restore(self, mission: Mission) -> Optional[Mission]:
        original_status = mission.status

        if mission.status == MissionStatus.RUNNING:
            # Interrupted — re-queue for a fresh run
            mission.status      = MissionStatus.QUEUED
            mission.started_at  = None
            mission.status_reason = "recovered from crash (was RUNNING)"
            logger.info("Recovery: mission %s RUNNING → QUEUED", mission.id)

        elif mission.status in (MissionStatus.QUEUED, MissionStatus.PAUSED, MissionStatus.BLOCKED):
            logger.info("Recovery: mission %s restored as %s", mission.id, mission.status.value)

        elif mission.is_terminal():
            # Terminal missions are returned as-is for portfolio history
            logger.debug("Recovery: mission %s already terminal (%s)", mission.id, mission.status.value)

        return mission

    def checkpoint(self, missions: dict[str, Mission]) -> int:
        """Checkpoint all non-terminal missions. Returns count saved."""
        return self._snap.save_all(missions)
