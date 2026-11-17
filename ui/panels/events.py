from __future__ import annotations

from typing import Any, Callable

from agent.events import AgentEvent, AgentEventType
class PanelEventAdapter:
    """Translate existing agent tool events into panel lifecycle operations."""

    def __init__(
        self,
        start: Callable[[str, str, str | None, dict[str, Any]], None],
        complete: Callable[[dict[str, Any]], None],
        tool_kind: Callable[[str], str | None],
    ) -> None:
        self._start = start
        self._complete = complete
        self._tool_kind = tool_kind

    def handle(self, event: AgentEvent) -> bool:
        """Consume tool lifecycle events; return False for unrelated events."""
        if event.type == AgentEventType.TOOL_CALL_START:
            data = event.data
            self._start(
                data.get("call_id", ""),
                data.get("name", "unknown"),
                self._tool_kind(data.get("name", "unknown")),
                data.get("arguments", {}),
            )
            return True
        if event.type == AgentEventType.TOOL_CALL_COMPLETE:
            self._complete(event.data)
            return True
        return False

