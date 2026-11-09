"""Responsive opening layouts for fresh and returning KRYTH sessions."""

from __future__ import annotations

from rich.align import Align
from rich.console import Group
from rich.table import Table
from rich.text import Text

from ui.components.logo import KRYTH_VERSION, horizontal_logo, logo, small_wordmark


def welcome_parts(
    *,
    model: str | None,
    workspace: str | None,
    session_id: str | None,
    branch: str | None,
    width: int,
    height: int = 24,
    unicode: bool = True,
    fresh: bool = True,
    last_task: str | None = None,
) -> list[object]:
    """Build the opening page from live metadata, adapting to the terminal."""
    width = max(12, width)
    height = max(8, height)
    full_hero = fresh and width >= 54 and height >= 23
    very_short = height < 18
    parts: list[object] = []

    if full_hero:
        parts.append(Align.center(logo(unicode=unicode), width=width))
        parts.append(Align.center(Text("AUTONOMOUS INTELLIGENCE", style="muted"), width=width))
        parts.append(
            Align.center(
                Text("Your workspace. Your agent. Your next build.", style="assistant"),
                width=width,
            )
        )
        parts.append(_divider(width, unicode))
    elif fresh:
        parts.append(
            horizontal_logo(
                unicode=unicode,
                tagline=width >= 54 and height >= 20,
            )
        )
        if width >= 48 and not very_short:
            parts.append(Align.center(Text("AUTONOMOUS INTELLIGENCE", style="muted"), width=width))
            parts.append(
                Align.center(
                    Text("Your workspace. Your agent. Your next build.", style="assistant"),
                    width=width,
                )
            )
    else:
        parts.append(horizontal_logo(unicode=unicode, tagline=False))

    fields: list[tuple[str, str]] = []
    if model and model.strip():
        fields.append(("MODEL", model.rsplit("/", 1)[-1]))
    if workspace and workspace.strip():
        fields.append(("WORKSPACE", workspace))
    if fresh and branch and width >= 54 and height >= 23:
        fields.append(("BRANCH", branch))
    if session_id:
        fields.append(("SESSION", session_id[:8]))
    if not very_short:
        fields.append(("VERSION", KRYTH_VERSION))
    if not fresh and last_task:
        fields.append(("LAST TASK", last_task))

    if very_short:
        fields = fields[:2]

    if fields:
        metadata = _metadata_table(fields, width)
        if full_hero:
            parts.append(Align.center(metadata, width=width))
            parts.append(_divider(width, unicode))
        else:
            parts.append(metadata)

    if fresh and not very_short and height >= 14:
        parts.append(
            Align.center(
                Text("What are we building today?", style="bold primary"),
                width=width,
            )
        )
        if width >= 46:
            support = "Describe your task. KRYTH will plan, execute, and verify."
        else:
            support = "Describe a task. KRYTH plans and verifies."
        parts.append(Align.center(Text(support, style="muted"), width=width))
        if width >= 68 and height >= 23:
            parts.extend(_actions(width))
        elif width >= 36:
            parts.append(Align.center(Text("/help   /model   /sessions   /config", style="muted"), width=width))

    return parts


def welcome_screen(**kwargs: object) -> Group:
    """Return a Rich group suitable for capture and terminal rendering."""
    return Group(*welcome_parts(**kwargs))


def _metadata_table(fields: list[tuple[str, str]], width: int) -> Table:
    compact = width < 34
    labels = {"MODEL": "M", "WORKSPACE": "WORK", "SESSION": "SESSION", "VERSION": "VER", "BRANCH": "BR"} if compact else {}
    table = Table.grid(padding=(0, 2))
    table.add_column(style="muted", no_wrap=True)
    table.add_column(style="primary", overflow="ellipsis")
    max_value = max(6, width - (14 if compact else 22))
    for key, value in fields:
        label = labels.get(key, key)
        table.add_row(label, _clip(value, max_value))
    return table


def _actions(width: int) -> list[object]:
    table = Table.grid(padding=(0, 3))
    table.add_column(no_wrap=True)
    table.add_column(style="muted", no_wrap=True)
    actions = [
        ("/help", "Explore commands"),
        ("/model", "Choose your model"),
        ("/sessions", "Continue previous work"),
    ]
    if width >= 82:
        actions.append(("/config", "Configure KRYTH"))
    for command, description in actions:
        table.add_row(Text(command, style="highlight"), description)
    return [Align.center(table, width=width)]


def _divider(width: int, unicode: bool) -> Align:
    char = "─" if unicode else "-"
    length = min(72, max(12, width - 16))
    return Align.center(Text(char * length, style="border"), width=width)


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3].rstrip() + "..."
