"""Team Controller — monitors worker utilisation and decides spawn/retire."""

from __future__ import annotations

import logging
from agent.executive.executive_state import (
    ExecutiveDecision, ExecutiveState, TeamState,
)

logger = logging.getLogger(__name__)

_MIN_PARALLEL_EFFICIENCY = 0.40   # retire if below this
_MAX_IDLE_RATIO          = 0.60   # spawn if idle > 60% of total
_SPAWN_THRESHOLD_PENDING = 5      # spawn extra workers if >5 pending actions


class TeamController:
    """Decides when to spawn more workers or retire idle ones."""

    def assess(self, state: ExecutiveState) -> ExecutiveDecision:
        t = state.health
        team = state.team

        if team.active_workers == 0:
            return ExecutiveDecision.CONTINUE

        idle_ratio = team.idle_workers / team.active_workers

        # Many pending + idle workers → spawn
        if t.pending > _SPAWN_THRESHOLD_PENDING and idle_ratio < 0.20:
            logger.info(
                "TeamController: %d pending actions, only %.0f%% idle — SPAWN",
                t.pending, idle_ratio * 100,
            )
            state.annotations["team_spawn"] = (
                f"{t.pending} pending, {idle_ratio:.0%} idle"
            )
            return ExecutiveDecision.SPAWN

        # Low parallel efficiency → retire idle workers
        if (team.parallel_efficiency_pct < _MIN_PARALLEL_EFFICIENCY * 100
                and idle_ratio > _MAX_IDLE_RATIO):
            logger.info(
                "TeamController: efficiency %.0f%%, idle %.0f%% — RETIRE",
                team.parallel_efficiency_pct, idle_ratio * 100,
            )
            state.annotations["team_retire"] = (
                f"efficiency {team.parallel_efficiency_pct:.0f}%, "
                f"idle {idle_ratio:.0%}"
            )
            return ExecutiveDecision.RETIRE

        return ExecutiveDecision.CONTINUE

    def update(self, state: ExecutiveState, **kwargs) -> None:
        t = state.team
        t.active_workers           = kwargs.get("active_workers", t.active_workers)
        t.idle_workers             = kwargs.get("idle_workers",   t.idle_workers)
        t.busy_workers             = kwargs.get("busy_workers",   t.busy_workers)
        t.parallel_peak            = max(t.parallel_peak, t.active_workers)
        t.parallel_efficiency_pct  = kwargs.get(
            "parallel_efficiency_pct", t.parallel_efficiency_pct
        )
