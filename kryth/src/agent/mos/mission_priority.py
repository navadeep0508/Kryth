"""Mission priority engine — computes a numeric score used for scheduling order."""

from __future__ import annotations

from agent.mos.mission_state import Mission, MissionPriorityLevel, priority_weight


class MissionPriority:
    """Computes a fine-grained numeric priority score for a mission.

    Score range: 0 – 200  (higher = scheduled sooner)
    Components:
        base_weight      (10 – 100 from enum)
        age_bonus        (up to +20 — older queued missions rise naturally)
        deadline_bonus   (up to +50 — imminent deadline)
        blocker_bonus    (+20 if other missions depend on this one)
        failure_penalty  (-15 if mission has already failed once before retry)
    """

    def compute(self, mission: Mission, now_s: float, queued_missions: list[Mission]) -> int:
        score = priority_weight(mission.priority)
        score += self._age_bonus(mission, now_s)
        score += self._deadline_bonus(mission, now_s)
        score += self._blocker_bonus(mission, queued_missions)
        score += self._failure_penalty(mission)
        return max(0, min(200, score))

    # ── Components ────────────────────────────────────────────────────

    @staticmethod
    def _age_bonus(mission: Mission, now_s: float) -> int:
        """Reward missions that have been waiting longest (anti-starvation)."""
        try:
            from datetime import datetime, timezone
            created = datetime.fromisoformat(mission.created_at).timestamp()
            wait_s  = now_s - created
            # +1 per 30 s waited, capped at +20
            return min(20, int(wait_s / 30))
        except Exception:
            return 0

    @staticmethod
    def _deadline_bonus(mission: Mission, now_s: float) -> int:
        """Boost missions with approaching deadlines."""
        if not mission.deadline:
            return 0
        try:
            from datetime import datetime, timezone
            deadline = datetime.fromisoformat(mission.deadline).timestamp()
            remaining_s = deadline - now_s
            if remaining_s <= 0:
                return 50     # overdue — max boost
            if remaining_s < 300:    # < 5 min
                return 40
            if remaining_s < 3600:   # < 1 h
                return 20
            if remaining_s < 86400:  # < 1 day
                return 10
            return 0
        except Exception:
            return 0

    @staticmethod
    def _blocker_bonus(mission: Mission, queued: list[Mission]) -> int:
        """Boost missions that other queued missions are waiting for."""
        if not mission.blocks:
            return 0
        blocked_ids = {m.id for m in queued}
        if any(bid in blocked_ids for bid in mission.blocks):
            return 20
        return 0

    @staticmethod
    def _failure_penalty(mission: Mission) -> int:
        if mission.metrics.actions_failed > 0:
            return -15
        return 0
