from typing import Any

from rich.table import Table

from ui.format import ordered_args, summarise_value


def render_args_table(tool_name: str, args: dict[str, Any]) -> Table:
    table = Table.grid(padding=(0, 1))
    table.add_column(style="muted", justify="right", no_wrap=True)
    table.add_column(style="code", overflow="fold")
    for key, value in ordered_args(tool_name, args):
        table.add_row(key, summarise_value(key, value))
    return table
