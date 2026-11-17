from __future__ import annotations

from collections import OrderedDict
from time import monotonic
from typing import Any

from ui.components.tool_call import ToolOutcome
from ui.format import format_elapsed
from ui.panels.registry import PanelRendererRegistry
from ui.panels.state import PanelState, PanelStatus

MAX_PANELS = 20


class PanelOrchestrator:
    """Owns one bounded, deduplicated panel lifecycle per agent call ID."""

    def __init__(self, registry: PanelRendererRegistry | None = None) -> None:
        self.registry = registry or PanelRendererRegistry()
        self._panels: OrderedDict[str, PanelState] = OrderedDict()
        self.active_call_id: str | None = None

    @property
    def panels(self) -> tuple[PanelState, ...]:
        return tuple(self._panels.values())

    @property
    def latest(self) -> PanelState | None:
        return next(reversed(self._panels.values()), None) if self._panels else None

    def start(
        self, call_id: str, name: str, tool_kind: str | None, arguments: dict[str, Any]
    ) -> tuple[PanelState, bool]:
        existing = self._panels.get(call_id)
        if existing is not None:
            self.active_call_id = call_id if existing.status == PanelStatus.RUNNING else None
            return existing, False
        state = PanelState(call_id, name, tool_kind, dict(arguments))
        self._panels[call_id] = state
        self.active_call_id = call_id
        self._trim()
        return state, True

    def update_progress(self, message: str) -> PanelState | None:
        state = self._panels.get(self.active_call_id or "")
        if state is not None and state.status == PanelStatus.RUNNING:
            state.progress = message
            return state
        return None

    def complete(
        self,
        call_id: str,
        *,
        success: bool,
        output: str,
        error: str | None,
        metadata: dict[str, Any] | None,
        truncated: bool,
        diff: str | None,
        exit_code: int | None,
        model_name: str = "",
    ) -> tuple[PanelState | None, bool]:
        state = self._panels.get(call_id)
        if state is None or state.status not in {
            PanelStatus.RUNNING,
            PanelStatus.WAITING_APPROVAL,
        }:
            return state, False
        state.status = PanelStatus.COMPLETED if success else PanelStatus.FAILED
        state.elapsed = format_elapsed(monotonic() - state.started_at)
        state.output = output
        state.error = error
        state.metadata = metadata or {}
        state.diff = diff
        state.exit_code = exit_code
        state.truncated = truncated
        outcome = ToolOutcome(
            name=state.name,
            args=state.arguments,
            success=success,
            output=output,
            error=error,
            metadata=state.metadata,
            truncated=truncated,
            diff=diff,
            exit_code=exit_code,
            model_name=model_name,
        )
        state.summary, state.details = self.registry.render(outcome)
        if self.active_call_id == call_id:
            self.active_call_id = None
        return state, True

    def cancel(self, call_id: str) -> PanelState | None:
        state = self._panels.get(call_id)
        if state is None or state.status not in {
            PanelStatus.RUNNING,
            PanelStatus.WAITING_APPROVAL,
        }:
            return state
        state.status = PanelStatus.CANCELLED
        state.elapsed = format_elapsed(monotonic() - state.started_at)
        if self.active_call_id == call_id:
            self.active_call_id = None
        return state

    def wait_for_approval(self, call_id: str | None = None) -> PanelState | None:
        target = call_id or self.active_call_id
        state = self._panels.get(target or "")
        if state is not None and state.status == PanelStatus.RUNNING:
            state.status = PanelStatus.WAITING_APPROVAL
        return state

    def resume_after_approval(self, call_id: str | None = None) -> PanelState | None:
        target = call_id or self.active_call_id
        state = self._panels.get(target or "")
        if state is not None and state.status == PanelStatus.WAITING_APPROVAL:
            state.status = PanelStatus.RUNNING
        return state

    def clear(self) -> None:
        """Remove every panel and reset the active call id."""
        self._panels.clear()
        self.active_call_id = None

    def _trim(self) -> None:
        while len(self._panels) > MAX_PANELS:
            oldest = next(iter(self._panels))
            if self._panels[oldest].status == PanelStatus.RUNNING:
                self._panels.move_to_end(oldest)
                if all(
                    panel.status in {PanelStatus.RUNNING, PanelStatus.WAITING_APPROVAL}
                    for panel in self._panels.values()
                ):
                    break
                continue
            del self._panels[oldest]

