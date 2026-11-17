from __future__ import annotations

from typing import Any, Callable

from ui.components.args_table import render_args_table
from ui.components.tool_call import (
    ARGS_TABLE_EXEMPT,
    Blocks,
    RENDERERS,
    ToolOutcome,
    _fallback,
    _subagent,
    tool_blocks,
)
from ui.format import headline, secondary_args
from tools.core import get_all_core_tools
from tools.subagents.subagents import get_default_subagent_definitions


Renderer = Callable[[ToolOutcome], Blocks]


class PanelRendererRegistry:
    """Presentation registry for core tools, subagents, and dynamic integrations."""

    def __init__(self) -> None:
        self._renderers: dict[str, Renderer] = {}
        for tool_class in get_all_core_tools():
            renderer = RENDERERS.get(tool_class.name)
            if renderer is not None:
                self._renderers[tool_class.name] = renderer
        self._subagents = {
            f"subagent_{definition.name}"
            for definition in get_default_subagent_definitions()
        }

    @property
    def registered_names(self) -> frozenset[str]:
        return frozenset(self._renderers) | frozenset(self._subagents)

    def register(self, name: str, renderer: Renderer) -> None:
        """Register a custom or MCP renderer without changing tool registration."""
        self._renderers[name] = renderer

    def render(self, outcome: ToolOutcome) -> Blocks:
        if not outcome.success:
            # Preserve the shared, bounded diagnostic renderer for every tool.
            return tool_blocks(outcome)
        renderer = self._renderers.get(outcome.name)
        if renderer is not None:
            summary, details = renderer(outcome)
        elif outcome.name in self._subagents or outcome.name.startswith("subagent_"):
            summary, details = _subagent(outcome)
        else:
            # MCP names are not known until a server connects; future tools remain safe.
            summary, details = _fallback(outcome)

        summary = [block for block in summary if block is not None]
        details = [block for block in details if block is not None]
        head = headline(outcome.args)
        secondary = secondary_args(outcome.args, head[0] if head else None)
        if secondary and outcome.name not in ARGS_TABLE_EXEMPT:
            details.insert(0, render_args_table(outcome.name, secondary))
        if outcome.truncated:
            from rich.text import Text

            summary.append(Text("Tool output was truncated", style="warning"))
        if not summary and not details:
            from rich.text import Text

            summary.append(Text("(no output)", style="muted"))
        return summary, details

