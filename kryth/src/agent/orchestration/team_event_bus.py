"""Team Event Bus — in-memory pub/sub for agent-to-agent discovery broadcasts.

Allows parallel same-domain agents to share discoveries without polling the
file system or re-reading the same code. Thread-safe; poll() is non-blocking.

Usage (in scheduler):
    bus = TeamEventBus()
    bus.subscribe("frontend_0")
    bus.subscribe("frontend_1")

    # From Frontend Agent #1 (when it discovers an API contract change):
    bus.publish("frontend_1", "api_change", {"detail": "POST /events now requires auth"})

    # Frontend Agent #0 polls at the start of its next turn:
    events = bus.poll("frontend_0")
    # → [{"from": "frontend_1", "type": "api_change", "payload": {...}}]
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BusEvent:
    from_agent: str
    event_type: str     # "discovery" | "api_change" | "conflict_warning" | "done"
    payload: Any        # free-form dict
    seq: int = 0        # monotonic sequence number


class TeamEventBus:
    """Thread-safe pub/sub bus for a single team's lifetime."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: Dict[str, deque] = {}
        self._seq = 0

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self, agent_id: str) -> None:
        """Register an agent to receive broadcasts. Idempotent."""
        with self._lock:
            if agent_id not in self._queues:
                self._queues[agent_id] = deque()

    def unsubscribe(self, agent_id: str) -> None:
        with self._lock:
            self._queues.pop(agent_id, None)

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(
        self,
        from_agent: str,
        event_type: str,
        payload: Any = None,
        *,
        exclude_self: bool = True,
    ) -> None:
        """Broadcast an event to all subscribed agents except the sender."""
        with self._lock:
            self._seq += 1
            event = BusEvent(
                from_agent=from_agent,
                event_type=event_type,
                payload=payload or {},
                seq=self._seq,
            )
            for aid, q in self._queues.items():
                if exclude_self and aid == from_agent:
                    continue
                q.append(event)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll(self, agent_id: str) -> List[BusEvent]:
        """Non-blocking drain of all pending events for agent_id.

        Returns events in arrival order and clears them. Agents should call
        this at the start of each turn to pick up sibling discoveries.
        """
        with self._lock:
            q = self._queues.get(agent_id)
            if not q:
                return []
            events = list(q)
            q.clear()
            return events

    def peek(self, agent_id: str) -> List[BusEvent]:
        """Non-destructive peek at pending events (does not clear)."""
        with self._lock:
            return list(self._queues.get(agent_id, []))

    def has_events(self, agent_id: str) -> bool:
        with self._lock:
            return bool(self._queues.get(agent_id))

    def clear(self) -> None:
        with self._lock:
            for q in self._queues.values():
                q.clear()

    def agent_count(self) -> int:
        with self._lock:
            return len(self._queues)

    # ------------------------------------------------------------------
    # Helper: format events as a prompt snippet
    # ------------------------------------------------------------------

    def format_for_prompt(self, events: List[BusEvent]) -> str:
        """Format polled events as a concise prompt injection."""
        if not events:
            return ""
        lines = ["Team discoveries from sibling agents:"]
        for e in events[:10]:  # cap at 10 to avoid prompt bloat
            detail = ""
            if isinstance(e.payload, dict):
                detail = e.payload.get("detail") or e.payload.get("message") or ""
            elif isinstance(e.payload, str):
                detail = e.payload
            lines.append(f"  [{e.event_type}] {e.from_agent}: {str(detail)[:200]}")
        return "\n".join(lines)
