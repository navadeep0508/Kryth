"""Browser Automation Dashboard.

Replaces raw OPEN URL / CLICK / SCREENSHOT spam with:

    Browser Verification

    ◈  Application loaded
    ◈  Navigation verified
    ◈  Dark mode tested
    ◈  UI responsive
    ◈  No browser errors
"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


def render_browser_step(label: str, status: str = "done", detail: str = "") -> None:
    """Render a single browser verification step inline."""
    glyph, style = {
        "done":    (CORE, "browser.step.done"),
        "running": (WAITING, "browser.step.running"),
        "failed":  (ERROR, "browser.step.failed"),
    }.get(status, (WAITING, "muted"))

    line = Text.assemble(
        ("  ", ""),
        (glyph, style),
        ("  ", ""),
        (label, style),
    )
    if detail:
        line.append(f"  {DOT}  {detail}", style="muted")
    console.print(line)


def render_browser_panel(steps: list, title: str = "Browser Verification") -> None:
    """Render a complete browser verification summary panel."""
    if not steps:
        return

    body = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(overflow="fold")
    body.add_column(overflow="fold", style="muted")

    for step in steps:
        label = step.get("label", "") if isinstance(step, dict) else str(step)
        status = step.get("status", "done") if isinstance(step, dict) else "done"
        detail = step.get("detail", "") if isinstance(step, dict) else ""

        glyph, style = {
            "done":    (CORE, "browser.step.done"),
            "running": (WAITING, "browser.step.running"),
            "failed":  (ERROR, "browser.step.failed"),
        }.get(status, (WAITING, "muted"))

        body.add_row(
            Text(glyph, style=style),
            Text(label, style=style),
            Text(detail, style="muted") if detail else Text(""),
        )

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), (f"  {title}", "eng.section")),
        title_align="left",
        border_style="browser.border",
        padding=(0, 2),
        expand=False,
    ))
