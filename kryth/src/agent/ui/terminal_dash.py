"""Terminal Layer — Abstracted Shell Output Dashboard.

Replaces raw npm/pip/cargo log floods with structured metric panels.

Before:
    (500 lines of npm output)

After:
    ╭─ npm install ──────────────────────────────────────╮
    │  ◈ Status        SUCCESS                           │
    │  ◈ Packages      42 installed                      │
    │  ◈ Warnings      2                                 │
    │  ◈ Errors        0                                 │
    │  ◈ Duration      16s                               │
    │  ◇ (expand to view full log)                       │
    ╰─────────────────────────────────────────────────────╯
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


def _status_glyph(status: str) -> tuple[str, str]:
    return {
        "success": (CORE, "term.success"),
        "failed":  (ERROR, "term.failed"),
        "running": (WAITING, "eng.running"),
    }.get(status, (WAITING, "muted"))


def render_terminal_summary(
    command: str,
    status: str,
    metrics: dict,
    expandable_log: str = "",
) -> None:
    """Render an abstracted terminal result panel."""
    glyph, glyph_style = _status_glyph(status)

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(no_wrap=True, min_width=16, style="term.metric.label")
    body.add_column(overflow="fold", style="term.metric.value")

    # Status row
    body.add_row(
        Text(glyph, style=glyph_style),
        Text("Status", style="term.metric.label"),
        Text(status.upper(), style=glyph_style),
    )

    # Metric rows
    for key, val in metrics.items():
        if val is None:
            continue
        label = key.replace("_", " ").title()
        val_str = str(val)
        style = "term.metric.value"
        if "error" in key.lower() and int(val) > 0:
            style = "term.failed"
        elif "warn" in key.lower() and int(val) > 0:
            style = "log.warn"
        elif "pass" in key.lower() or "success" in key.lower():
            style = "term.success"
        body.add_row(
            Text("", style=""),
            Text(label, style="term.metric.label"),
            Text(val_str, style=style),
        )

    # Expand hint if there's a log
    if expandable_log:
        body.add_row(
            Text(WAITING, style="muted"),
            Text("", style=""),
            Text("(expand to view full log)", style="term.expand"),
        )

    border = "term.success" if status == "success" else "term.failed" if status == "failed" else "divider"

    short_cmd = command if len(command) <= 70 else command[:67] + "..."
    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((glyph, glyph_style), (f"  {short_cmd}", "title")),
        title_align="left",
        border_style=border,
        padding=(0, 2),
        expand=False,
    ))


def render_terminal_section(results: list) -> None:
    """Render a compact terminal results section."""
    if not results:
        return

    lines = []
    for r in results[-5:]:
        glyph, style = _status_glyph(r.status if hasattr(r, "status") else r.get("status", ""))
        cmd = r.command if hasattr(r, "command") else r.get("command", "")
        short = cmd if len(cmd) <= 60 else cmd[:57] + "..."
        lines.append(Text.assemble(
            (glyph, style), ("  ", ""), (short, "title")
        ))

    body = Group(*lines)
    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Terminal", "eng.section")),
        title_align="left",
        border_style="divider",
        padding=(0, 2),
        expand=False,
    ))
