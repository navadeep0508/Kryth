"""DAG Visualization — Execution graph with active node highlighting.

Repository Scan
      ↓
Intent Analysis
      ↓  ← active
Capability Graph
      ↓
Task DAG
"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


_STATUS_GLYPH = {
    "done":    (CORE, "dag.done"),
    "active":  (CORE, "dag.active"),
    "failed":  (ERROR, "dag.failed"),
    "pending": (WAITING, "dag.pending"),
}


def render_dag(nodes: list) -> None:
    """Render the execution DAG as a vertical node list with arrows."""
    if not nodes:
        return

    body = Table(show_header=False, box=None, padding=(0, 1), expand=False)
    body.add_column(no_wrap=True, min_width=3)
    body.add_column(overflow="fold")

    for i, node in enumerate(nodes):
        node_id = node.id if hasattr(node, "id") else node.get("id", "")
        label = node.label if hasattr(node, "label") else node.get("label", "")
        status = node.status if hasattr(node, "status") else node.get("status", "pending")
        active = node.active if hasattr(node, "active") else node.get("active", False)

        if active:
            glyph, style = CORE, "dag.active"
        else:
            glyph, style = _STATUS_GLYPH.get(status, (WAITING, "dag.pending"))

        label_style = "dag.active" if active else style

        body.add_row(
            Text(glyph, style=glyph),
            Text(label, style=label_style),
        )

        # Arrow between nodes (except last)
        if i < len(nodes) - 1:
            body.add_row(
                Text("│", style="dag.arrow"),
                Text("↓", style="dag.arrow"),
            )

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Execution Graph", "eng.section")),
        title_align="left",
        border_style="divider",
        padding=(0, 2),
        expand=False,
    ))
