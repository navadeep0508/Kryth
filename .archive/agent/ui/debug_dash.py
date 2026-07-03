"""Debug Layer — Raw execution details, hidden by default.

Contains: tool calls, raw terminal, stack traces, LLM context, timings.
Only visible when layer=debug or KRYTH_DEBUG_UI=1.
"""

from __future__ import annotations

import os

from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


_DEBUG_UI = os.environ.get("KRYTH_DEBUG_UI", "").lower() in {"1", "true", "yes"}


def is_debug_mode() -> bool:
    from agent.ui.ui_state import ui_state, UILayer
    return _DEBUG_UI or ui_state.get_layer() == UILayer.DEBUG


def render_debug_tool_call(name: str, args: dict, result: str, duration_ms: float) -> None:
    if not is_debug_mode():
        return

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=10)
    body.add_column(overflow="fold")

    body.add_row("tool", Text(name, style="tool.name"))
    body.add_row("duration", Text(f"{duration_ms:.1f}ms", style="muted"))

    # Args — show key/value pairs
    if args:
        for k, v in list(args.items())[:4]:
            val = str(v)
            if len(val) > 80:
                val = val[:77] + "..."
            body.add_row(f"  {k}", Text(val, style="tool.arg"))

    # Result preview
    result_preview = result[:200] if len(result) > 200 else result
    body.add_row("result", Text(result_preview, style="tool.result"))

    console.print(Panel(
        body,
        title=Text.assemble((CORE, "layer.debug"), ("  DEBUG · TOOL", "layer.debug")),
        title_align="left",
        border_style="divider",
        padding=(0, 1),
        expand=False,
    ))


def render_debug_log_entries(entries: list) -> None:
    """Render recent debug log entries as a table."""
    if not entries:
        return

    body = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="timeline.time", min_width=10)
    body.add_column(no_wrap=True, style="muted", min_width=12)
    body.add_column(overflow="fold")

    for entry in entries[-20:]:
        kind = entry.get("kind", "log")
        msg = str(entry.get("data", {}).get("message", "") or
                  entry.get("data", {}).get("name", ""))[:100]
        ts = entry.get("ts_str", "")
        style = {
            "tool.start": "tool.name",
            "tool.error": "tool.error",
            "shell.end": "term.metric.value",
            "llm.error": "log.error",
        }.get(kind, "muted")
        body.add_row(ts, Text(kind, style="layer.debug"), Text(msg, style=style))

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "layer.debug"), ("  Debug Log", "layer.debug")),
        title_align="left",
        border_style="divider",
        padding=(0, 1),
        expand=False,
    ))
