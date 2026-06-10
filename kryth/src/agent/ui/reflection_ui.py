"""Reflection UI — Post-task learning display.

    ╭─ Reflection ─────────────────────────────────────────╮
    │                                                      │
    │  ◈ What Worked      Build workflow                   │
    │  ◆ What Failed      Missing package.json             │
    │  ◇ What Was Learned React project missing metadata   │
    │                                                      │
    │  Store permanently? [Y] [N]                          │
    │                                                      │
    ╰──────────────────────────────────────────────────────╯
"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, ERROR, WAITING


def render_reflection(worked: str, failed: str, learned: str) -> None:
    """Render a reflection panel after task completion."""
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(no_wrap=True, style="muted", min_width=18)
    body.add_column(overflow="fold")

    if worked:
        body.add_row(
            Text(CORE, style="reflect.worked"),
            Text("What Worked", style="muted"),
            Text(worked, style="reflect.worked"),
        )
    if failed:
        body.add_row(
            Text(ERROR, style="reflect.failed"),
            Text("What Failed", style="muted"),
            Text(failed, style="reflect.failed"),
        )
    if learned:
        body.add_row(
            Text(WAITING, style="reflect.learned"),
            Text("What Was Learned", style="muted"),
            Text(learned, style="reflect.learned"),
        )

    body.add_row(Text(""), Text(""), Text(""))  # spacer

    store_line = Text.assemble(
        ("Store permanently? ", "muted"),
        ("[Y]", "motion.hot"), (" Yes  ", "muted"),
        ("[N]", "kbd"), (" No", "muted"),
    )
    body.add_row(Text(""), Text(""), store_line)

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "reflect.learned"), ("  Reflection", "title")),
        title_align="left",
        border_style="reflect.border",
        padding=(1, 2),
        expand=False,
    ))
