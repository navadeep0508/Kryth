from typing import Any

from rich.table import Table
from rich.text import Text


def render_memory(metadata: dict[str, Any]) -> Table | Text:
    entries = metadata.get("entries") or []
    if not entries:
        return Text("No memory stored.", style="muted")

    active_key = metadata.get("active_key")

    table = Table.grid(padding=(0, 1))
    table.add_column(no_wrap=True)
    table.add_column(style="info", no_wrap=True)
    table.add_column(overflow="fold")

    for entry in entries:
        key = str(entry.get("key", ""))
        is_active = active_key is not None and key == active_key
        marker_style = "primary" if is_active else "muted"
        value_style = "primary" if is_active else "code"
        table.add_row(
            Text("*" if is_active else "", style=marker_style),
            Text(key),
            Text(str(entry.get("value", "")), style=value_style),
        )
    return table
