"""Activity Timeline — Live ordered event log.

Renders a timestamped activity timeline:

    10:21:01  ◈ Repository detected
    10:21:03  ◈ React + Vite identified
    10:21:05  ◆ Missing package.json
    10:21:07  ◈ Repair strategy generated
    10:21:12  ◇ Waiting for approval
    10:21:16  ◈ Creating package.json
    10:21:28  ◈ Installing dependencies
    10:21:43  ◈ Running browser verification
    10:21:50  ◈ Mission completed
"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


_KIND_GLYPH: dict[str, tuple[str, str]] = {
    "info":    (CORE, "timeline.info"),
    "success": (CORE, "timeline.success"),
    "warn":    (ERROR, "timeline.warn"),
    "error":   (ERROR, "timeline.error"),
}


def _glyph(kind: str) -> tuple[str, str]:
    return _KIND_GLYPH.get(kind, (WAITING, "muted"))


def render_timeline(entries: list, *, compact: bool = False) -> None:
    """Render the full activity timeline panel."""
    if not entries:
        return

    body = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="timeline.time", min_width=9)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(overflow="fold")

    shown = entries[-15:] if compact else entries
    for entry in shown:
        kind = entry.kind if hasattr(entry, "kind") else entry.get("kind", "info")
        message = entry.message if hasattr(entry, "message") else entry.get("message", "")
        time_str = entry.time_str if hasattr(entry, "time_str") else entry.get("time_str", "")
        glyph, glyph_style = _glyph(kind)

        msg_style = {
            "info": "timeline.info",
            "success": "timeline.success",
            "warn": "timeline.warn",
            "error": "timeline.error",
        }.get(kind, "muted")

        body.add_row(
            Text(time_str, style="timeline.time"),
            Text(glyph, style=glyph_style),
            Text(message, style=msg_style),
        )

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Activity Timeline", "eng.section")),
        title_align="left",
        border_style="divider",
        padding=(0, 1),
        expand=False,
    ))


def append_timeline_line(message: str, kind: str = "info", time_str: str = "") -> None:
    """Print a single timeline line to the console (live append mode)."""
    glyph, glyph_style = _glyph(kind)
    msg_style = {
        "info": "timeline.info",
        "success": "timeline.success",
        "warn": "timeline.warn",
        "error": "timeline.error",
    }.get(kind, "muted")

    console.print(Text.assemble(
        (time_str or "        ", "timeline.time"),
        ("  ", ""),
        (glyph, glyph_style),
        ("  ", ""),
        (message, msg_style),
    ))
