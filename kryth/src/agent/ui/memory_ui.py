"""Memory UI — Experience Engine display.

    ╭─ Experience Engine ──────────────────────────────────╮
    │                                                      │
    │  ◈ Found             12 similar tasks                │
    │  ◈ Best Workflow     94% success                     │
    │  ◈ Best Team         Backend · Frontend · Testing    │
    │                                                      │
    ╰──────────────────────────────────────────────────────╯
"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT


def render_memory(similar_tasks: int, best_workflow: str, success_rate: int) -> None:
    """Render the memory/experience engine panel."""
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(no_wrap=True, style="muted", min_width=20)
    body.add_column(overflow="fold")

    body.add_row(
        Text(CORE, style="memory.count"),
        Text("Found", style="muted"),
        Text(f"{similar_tasks} similar tasks", style="memory.count"),
    )
    if best_workflow:
        body.add_row(
            Text(CORE, style="memory.workflow"),
            Text("Best Workflow", style="muted"),
            Text(f"{best_workflow}  {DOT}  {success_rate}% success", style="memory.rate"),
        )

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "memory.count"), ("  Experience Engine", "title")),
        title_align="left",
        border_style="memory.border",
        padding=(1, 2),
        expand=False,
    ))
