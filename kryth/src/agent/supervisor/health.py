"""Health Engine — real-time subsystem health monitoring.

Computes a 0–100 health score for each subsystem and an overall
mission health percentage. Published to UIStateManager every tick.

Health sources (each probed lazily, failures degrade gracefully):
  - Agent health    → AgentSupervisor
  - Terminal health → process uptime + exit codes
  - Browser health  → browser session alive flag
  - Memory health   → token budget remaining
  - Workflow health → step success rate so far
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class SubsystemHealth:
    name: str
    score: int          # 0-100
    status: str         # healthy | degraded | critical | unknown
    detail: str = ""
    last_checked: float = field(default_factory=time.monotonic)

    @property
    def is_healthy(self) -> bool:
        return self.score >= 70

    @property
    def is_critical(self) -> bool:
        return self.score < 40


@dataclass
class SystemHealth:
    overall: int                              # 0-100 weighted average
    subsystems: Dict[str, SubsystemHealth]   # name → SubsystemHealth
    risk_level: str                           # low | medium | high | critical
    timestamp: float = field(default_factory=time.time)

    @property
    def label(self) -> str:
        if self.overall >= 90:
            return "Excellent"
        if self.overall >= 70:
            return "Healthy"
        if self.overall >= 50:
            return "Degraded"
        return "Critical"


_WEIGHTS = {
    "agents":   0.35,
    "terminal": 0.25,
    "browser":  0.15,
    "budget":   0.15,
    "workflow": 0.10,
}


class HealthEngine:
    """Compute and publish real-time health scores for all subsystems."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subsystems: Dict[str, SubsystemHealth] = {}
        self._last_system: Optional[SystemHealth] = None

    # ------------------------------------------------------------------
    # Update individual subsystems

    def update(self, name: str, score: int, detail: str = "") -> None:
        """Directly set a subsystem health score (0-100)."""
        score = max(0, min(100, score))
        if score >= 70:
            status = "healthy"
        elif score >= 40:
            status = "degraded"
        else:
            status = "critical"
        with self._lock:
            self._subsystems[name] = SubsystemHealth(
                name=name, score=score, status=status, detail=detail
            )

    def mark_unknown(self, name: str) -> None:
        with self._lock:
            self._subsystems[name] = SubsystemHealth(
                name=name, score=100, status="unknown"
            )

    # ------------------------------------------------------------------
    # Snapshot

    def snapshot(self) -> SystemHealth:
        """Compute and return the current system health."""
        with self._lock:
            subs = dict(self._subsystems)

        # Weighted average of known subsystems
        total_weight = 0.0
        weighted_score = 0.0
        for name, weight in _WEIGHTS.items():
            if name in subs:
                sub = subs[name]
                if sub.status != "unknown":
                    weighted_score += sub.score * weight
                    total_weight += weight

        overall = int(weighted_score / total_weight) if total_weight else 100

        # Risk level
        if overall >= 85:
            risk = "low"
        elif overall >= 65:
            risk = "medium"
        elif overall >= 40:
            risk = "high"
        else:
            risk = "critical"

        sys_health = SystemHealth(
            overall=overall, subsystems=subs, risk_level=risk
        )
        with self._lock:
            self._last_system = sys_health
        return sys_health

    def probe_and_publish(self) -> SystemHealth:
        """Probe all live subsystems, update health, publish to UI."""
        self._probe_agents()
        self._probe_terminal()
        self._probe_browser()
        self._probe_budget()
        health = self.snapshot()
        self._publish(health)
        return health

    # ------------------------------------------------------------------
    # Live probes

    def _probe_agents(self) -> None:
        try:
            from agent.supervisor.agent_supervisor import agent_supervisor
            statuses = agent_supervisor.all_statuses()
            if not statuses:
                self.mark_unknown("agents")
                return
            total = len(statuses)
            healthy = sum(1 for s in statuses if s.is_healthy)
            score = int(healthy / total * 100) if total else 100
            failed = [s.agent_id for s in statuses if s.status == "failed"]
            detail = f"failed: {', '.join(failed[:3])}" if failed else ""
            self.update("agents", score, detail)
        except Exception:
            self.mark_unknown("agents")

    def _probe_terminal(self) -> None:
        try:
            from agent.supervisor.terminal_supervisor import terminal_supervisor
            score = terminal_supervisor.health_score()
            self.update("terminal", score)
        except Exception:
            self.mark_unknown("terminal")

    def _probe_browser(self) -> None:
        try:
            from agent.supervisor.browser_supervisor import browser_supervisor
            score = browser_supervisor.health_score()
            self.update("browser", score)
        except Exception:
            self.mark_unknown("browser")

    def _probe_budget(self) -> None:
        try:
            from agent.supervisor.budget import budget_controller
            snap = budget_controller.snapshot()
            # Budget health = remaining % of token budget
            if snap.token_limit > 0:
                remaining_pct = int(snap.tokens_remaining / snap.token_limit * 100)
                score = max(0, min(100, remaining_pct))
            else:
                score = 100
            self.update("budget", score,
                        f"{snap.tokens_spent:,} tokens used / {snap.token_limit:,}")
        except Exception:
            self.mark_unknown("budget")

    def _publish(self, health: SystemHealth) -> None:
        try:
            from agent.ui.ui_state import ui_state
            # Map health to mission progress heuristic
            ui_state.mission_progress(health.overall, health.label)
        except Exception:
            pass


# Module-level singleton
health_engine = HealthEngine()
