"""Mission Scheduler — decides which queued mission to start next.

Scheduling algorithm:
1. Refresh priority scores (age bonuses, deadline proximity)
2. Filter missions whose dependencies are all satisfied
3. Check resource availability via ResourceManager
4. Pick the highest-scoring mission
5. Admit via MissionAllocator
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from agent.mos.mission_state import Mission, MissionStatus
from agent.mos.mission_queue import MissionQueue
from agent.mos.mission_allocator import MissionAllocator
from agent.mos.mission_dependencies import MissionDependencies
from agent.mos.resource_manager import ResourceManager

logger = logging.getLogger(__name__)


class MissionScheduler:

    def __init__(
        self,
        queue: MissionQueue,
        allocator: MissionAllocator,
        dependencies: MissionDependencies,
        resource_manager: ResourceManager,
        max_concurrent: int = 4,
    ) -> None:
        self._queue       = queue
        self._allocator   = allocator
        self._deps        = dependencies
        self._resources   = resource_manager
        self._max_concurrent = max_concurrent

    # ── Main schedule loop ────────────────────────────────────────────

    def tick(self, running: dict[str, Mission]) -> list[Mission]:
        """Attempt to start new missions. Returns list of newly admitted missions."""
        if len(running) >= self._max_concurrent:
            logger.debug("Scheduler: at capacity (%d/%d)", len(running), self._max_concurrent)
            return []

        self._queue.refresh_priorities()
        slots = self._max_concurrent - len(running)
        admitted: list[Mission] = []

        all_queued = self._queue.all_queued()
        for mission in all_queued:
            if len(admitted) >= slots:
                break
            if not self._deps.is_ready(mission.id):
                logger.debug("Scheduler: mission %s blocked by deps", mission.id)
                continue

            ok, reason = self._allocator.can_start(mission)
            if not ok:
                logger.debug("Scheduler: mission %s not admissible: %s", mission.id, reason)
                continue

            if self._allocator.admit(mission):
                self._queue.remove(mission.id)
                admitted.append(mission)
                logger.info(
                    "Scheduler: admitted mission %s (%s) score=%d",
                    mission.id, mission.name, mission.priority_score,
                )

        return admitted

    def enqueue(self, mission: Mission) -> None:
        """Register dependencies and push to priority queue."""
        self._deps.register(mission)
        if mission.status != MissionStatus.QUEUED:
            mission.status = MissionStatus.QUEUED
        self._queue.push(mission)

    def on_completed(
        self,
        mission: Mission,
        portfolio: dict[str, Mission],
    ) -> list[str]:
        """Handle completion: unblock waiters, return unblocked ids."""
        self._allocator.release(mission)
        unblocked = self._deps.on_mission_completed(mission.id, portfolio)
        # Re-queue unblocked missions
        for mid in unblocked:
            blocked_m = portfolio.get(mid)
            if blocked_m and blocked_m.status == MissionStatus.BLOCKED:
                blocked_m.status = MissionStatus.QUEUED
                self._queue.push(blocked_m)
                logger.info("Scheduler: mission %s unblocked by %s", mid, mission.id)
        return unblocked

    def on_failed(
        self,
        mission: Mission,
        portfolio: dict[str, Mission],
    ) -> list[str]:
        """Handle failure: cascade to dependents."""
        self._allocator.release(mission)
        cascade = self._deps.on_mission_failed(mission.id, portfolio)
        for mid in cascade:
            m = portfolio.get(mid)
            if m:
                m.status = MissionStatus.FAILED
                m.status_reason = f"prerequisite {mission.id} failed"
                self._queue.remove(mid)
                logger.warning("Scheduler: cascaded failure to mission %s", mid)
        return cascade

    def utilization_summary(self) -> dict:
        return {
            "queued":           self._queue.size(),
            "max_concurrent":   self._max_concurrent,
            "resources":        self._resources.utilization(),
        }
