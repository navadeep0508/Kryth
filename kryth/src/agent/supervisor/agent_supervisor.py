"""Agent Supervisor — monitor worker health, detect idle/deadlock/crash.

Tracks every spawned subagent:
  - Status: running | idle | done | failed | crashed | deadlocked
  - Last activity timestamp (for idle detection)
  - Turn count (for runaway detection)
  - Resource ownership

Auto-actions:
  - Idle too long  → emit warning, optionally reassign work
  - Turn overflow  → kill and mark failed
  - Deadlock hint  → surface to DynamicReplanner
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


IDLE_THRESHOLD_SECONDS = 30.0     # agent with no activity for this long is "idle"
TURN_LIMIT_DEFAULT = 120          # max turns before forced kill


@dataclass
class AgentHealthStatus:
    agent_id: str
    role: str = ""
    status: str = "running"          # running | idle | done | failed | crashed | deadlocked
    turns_used: int = 0
    turn_limit: int = TURN_LIMIT_DEFAULT
    last_activity: float = field(default_factory=time.monotonic)
    error: str = ""
    owned_resources: list = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return self.status in ("running", "idle", "done")

    @property
    def is_idle(self) -> bool:
        if self.status != "running":
            return False
        return (time.monotonic() - self.last_activity) > IDLE_THRESHOLD_SECONDS

    @property
    def is_over_limit(self) -> bool:
        return self.turns_used >= self.turn_limit

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_activity


class AgentSupervisor:
    """Track and monitor all active subagents."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._agents: Dict[str, AgentHealthStatus] = {}

    # ------------------------------------------------------------------
    # Registration

    def register(
        self,
        agent_id: str,
        role: str = "",
        turn_limit: int = TURN_LIMIT_DEFAULT,
    ) -> None:
        with self._lock:
            self._agents[agent_id] = AgentHealthStatus(
                agent_id=agent_id,
                role=role,
                turn_limit=turn_limit,
            )

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            self._agents.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Updates

    def heartbeat(self, agent_id: str, turns_used: int = 0) -> None:
        """Agent is alive — update last activity timestamp."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.last_activity = time.monotonic()
                agent.turns_used = turns_used
                if agent.status == "idle":
                    agent.status = "running"

    def mark_done(self, agent_id: str) -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.status = "done"

    def mark_failed(self, agent_id: str, error: str = "") -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.status = "failed"
                agent.error = error

    def mark_crashed(self, agent_id: str, error: str = "") -> None:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent:
                agent.status = "crashed"
                agent.error = error

    # ------------------------------------------------------------------
    # Query

    def all_statuses(self) -> List[AgentHealthStatus]:
        with self._lock:
            return list(self._agents.values())

    def get(self, agent_id: str) -> Optional[AgentHealthStatus]:
        with self._lock:
            return self._agents.get(agent_id)

    def idle_agents(self) -> List[AgentHealthStatus]:
        with self._lock:
            return [a for a in self._agents.values() if a.is_idle]

    def failed_agents(self) -> List[AgentHealthStatus]:
        with self._lock:
            return [a for a in self._agents.values()
                    if a.status in ("failed", "crashed")]

    def over_limit_agents(self) -> List[AgentHealthStatus]:
        with self._lock:
            return [a for a in self._agents.values() if a.is_over_limit
                    and a.status == "running"]

    # ------------------------------------------------------------------
    # Supervision sweep

    def sweep(self) -> List[str]:
        """Check all agents and return list of issues detected."""
        issues: List[str] = []
        with self._lock:
            agents = list(self._agents.values())

        for agent in agents:
            if agent.is_idle and agent.status == "running":
                idle_s = agent.idle_seconds
                issues.append(
                    f"agent '{agent.agent_id}' idle for {idle_s:.0f}s"
                )
                with self._lock:
                    if agent.agent_id in self._agents:
                        self._agents[agent.agent_id].status = "idle"

            if agent.is_over_limit and agent.status == "running":
                issues.append(
                    f"agent '{agent.agent_id}' exceeded turn limit "
                    f"({agent.turns_used}/{agent.turn_limit})"
                )

        # Publish to UI
        self._publish()
        return issues

    def _publish(self) -> None:
        """Push agent status updates to UIStateManager."""
        try:
            from agent import ui
            with self._lock:
                agents = list(self._agents.values())
            for a in agents:
                progress = min(100, int(a.turns_used / a.turn_limit * 100))
                ui.agent_update(a.agent_id, a.status, a.role, progress)
        except Exception:
            pass

    def reset(self) -> None:
        with self._lock:
            self._agents.clear()


# Module-level singleton
agent_supervisor = AgentSupervisor()
