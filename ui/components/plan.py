from typing import Any

from rich.text import Text
from rich.console import Group

MARKERS = {
    "completed": ("[done]", "success"),
    "in_progress": ("[work]", "highlight"),
    "pending": ("[next]", "muted"),
}


def render_plan(metadata: dict[str, Any]) -> Group | Text:
    steps = metadata.get("steps") or []
    if not steps:
        return Text("No plan steps.", style="muted")

    completed = metadata.get("completed", 0)
    total = metadata.get("total", len(steps))
    rows: list[Text] = [Text(f"PLAN  {completed}/{total} completed", style="highlight")]
    for step in steps:
        marker, content_style = MARKERS.get(
            str(step.get("status")), MARKERS["pending"]
        )
        row = Text("  ")
        row.append(marker, style=content_style)
        row.append(" ")
        row.append(str(step.get("content", "")), style=content_style)
        rows.append(row)
    return Group(*rows)
