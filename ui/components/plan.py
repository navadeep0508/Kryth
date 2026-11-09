from typing import Any

from rich.table import Table
from rich.text import Text

MARKERS = {
    "completed": ("✔", "success", "muted strike"),
    "in_progress": ("▶", "info", "highlight"),
    "pending": ("☐", "muted", "code"),
}


def render_plan(metadata: dict[str, Any]) -> Table | Text:
    steps = metadata.get("steps") or []
    if not steps:
        return Text("No plan steps.", style="muted")

    checklist = Table.grid(padding=(0, 1), expand=True)
    checklist.add_column(style="border", no_wrap=True)
    checklist.add_column(no_wrap=True)
    checklist.add_column(overflow="fold", ratio=1)
    checklist.add_column(style="dim", no_wrap=True, justify="right")

    last = len(steps) - 1
    for index, step in enumerate(steps):
        marker, marker_style, content_style = MARKERS.get(
            str(step.get("status")), MARKERS["pending"]
        )
        checklist.add_row(
            "╰─" if index == last else "├─",
            Text(marker, style=marker_style),
            Text(str(step.get("content", "")), style=content_style),
            Text(str(step.get("id", ""))),
        )
    return checklist
