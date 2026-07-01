"""Mission Dependencies — tracks inter-mission blocking relationships."""

from __future__ import annotations


from typing import Optional

from agent.mos.mission_state import Mission, MissionStatus


class MissionDependencies:
    """Tracks which missions are blocked by which.

    Rules:
    - A mission moves from QUEUED → can only run when all depends_on are COMPLETED.
    - When a prerequisite FAILS, all dependents become FAILED (cascade).
    - When a prerequisite is ARCHIVED, dependents are unblocked anyway.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # id → set of mission ids this mission depends on (still incomplete)
        self._pending_deps: dict[str, set[str]] = {}

    # ── Public API ────────────────────────────────────────────────────

    def register(self, mission: Mission) -> None:
        with self._lock:
            if mission.depends_on:
                self._pending_deps[mission.id] = set(mission.depends_on)
            else:
                self._pending_deps[mission.id] = set()

    def unregister(self, mission_id: str) -> None:
        with self._lock:
            self._pending_deps.pop(mission_id, None)

    def is_ready(self, mission_id: str) -> bool:
        """Return True if all dependencies are satisfied."""
        with self._lock:
            return len(self._pending_deps.get(mission_id, set())) == 0

    def on_mission_completed(
        self,
        completed_id: str,
        portfolio: dict[str, Mission],
    ) -> list[str]:
        """Mark completed_id as done and return ids newly unblocked."""
        unblocked: list[str] = []
        with self._lock:
            for mid, pending in self._pending_deps.items():
                if completed_id in pending:
                    pending.discard(completed_id)
                    if not pending:
                        unblocked.append(mid)
        return unblocked

    def on_mission_failed(
        self,
        failed_id: str,
        portfolio: dict[str, Mission],
    ) -> list[str]:
        """Cascade failure: return ids that must be failed because of failed_id."""
        cascade: list[str] = []
        with self._lock:
            for mid, pending in self._pending_deps.items():
                if failed_id in pending:
                    cascade.append(mid)
        return cascade

    def blocked_by(self, mission_id: str) -> set[str]:
        with self._lock:
            return set(self._pending_deps.get(mission_id, set()))

    def status_summary(self) -> dict[str, list[str]]:
        with self._lock:
            return {mid: list(deps) for mid, deps in self._pending_deps.items() if deps}
