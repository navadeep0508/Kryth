"""Mission Queue — thread-safe priority queue over Mission objects."""

from __future__ import annotations

import heapq

import time
from typing import Optional

from agent.mos.mission_state import Mission, MissionStatus
from agent.mos.mission_priority import MissionPriority


class MissionQueue:
    """Priority-ordered queue of missions awaiting a scheduler slot.

    Internally uses a min-heap on (-priority_score, created_at) so the
    highest-priority, oldest mission always pops first.
    """

    def __init__(self) -> None:
        self._heap:  list[tuple[int, str, str]] = []  # (-score, created_at, mission_id)
        self._index: dict[str, Mission]          = {}  # id → Mission
        self._lock   = threading.Lock()
        self._priori = MissionPriority()

    # ── Public API ────────────────────────────────────────────────────

    def push(self, mission: Mission) -> None:
        """Enqueue a mission. Re-computes priority score first."""
        with self._lock:
            self._recompute_score(mission)
            heapq.heappush(
                self._heap,
                (-mission.priority_score, mission.created_at, mission.id),
            )
            self._index[mission.id] = mission

    def pop(self) -> Optional[Mission]:
        """Return the highest-priority ready mission, or None if empty."""
        with self._lock:
            while self._heap:
                _, _, mid = heapq.heappop(self._heap)
                mission = self._index.get(mid)
                if mission and mission.status == MissionStatus.QUEUED:
                    return mission
                # Otherwise: removed or no longer queued — skip
            return None

    def peek(self) -> Optional[Mission]:
        """Return the highest-priority mission without removing it."""
        with self._lock:
            for _, _, mid in self._heap:
                m = self._index.get(mid)
                if m and m.status == MissionStatus.QUEUED:
                    return m
            return None

    def remove(self, mission_id: str) -> bool:
        """Remove a mission from the queue (lazy removal — cleared on pop)."""
        with self._lock:
            if mission_id in self._index:
                del self._index[mission_id]
                return True
            return False

    def requeue(self, mission: Mission) -> None:
        """Update priority score and re-insert (e.g., after priority change)."""
        with self._lock:
            self._index.pop(mission.id, None)
        mission.status = MissionStatus.QUEUED
        self.push(mission)

    def all_queued(self) -> list[Mission]:
        """Return all currently queued missions, sorted by priority."""
        with self._lock:
            return sorted(
                [m for m in self._index.values() if m.status == MissionStatus.QUEUED],
                key=lambda m: -m.priority_score,
            )

    def size(self) -> int:
        with self._lock:
            return sum(1 for m in self._index.values() if m.status == MissionStatus.QUEUED)

    def empty(self) -> bool:
        return self.size() == 0

    def refresh_priorities(self) -> None:
        """Recompute all priority scores (call periodically for age bonuses)."""
        with self._lock:
            queued = [m for m in self._index.values() if m.status == MissionStatus.QUEUED]
            for m in queued:
                self._recompute_score(m, all_queued=queued)
            # Rebuild heap
            self._heap = [
                (-m.priority_score, m.created_at, m.id) for m in queued
            ]
            heapq.heapify(self._heap)

    # ── Internals ─────────────────────────────────────────────────────

    def _recompute_score(
        self,
        mission: Mission,
        all_queued: Optional[list[Mission]] = None,
    ) -> None:
        if all_queued is None:
            all_queued = [m for m in self._index.values() if m.status == MissionStatus.QUEUED]
        score = self._priori.compute(mission, time.time(), all_queued)
        mission.priority_score = score
