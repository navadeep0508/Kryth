"""Responsive KRYTH startup screen."""

from __future__ import annotations

from pathlib import PureWindowsPath

from rich.align import Align
from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from ui.components.logo import KRYTH_VERSION, LOGO_WIDTH, logo_lines, small_wordmark
from ui.theme.palette import PALETTE


def welcome_parts(
    *,
    model: str | None,
    workspace: str | None,
    provider: str | None = None,
    session_id: str | None = None,
    branch: str | None,
    width: int,
    height: int = 24,
    unicode: bool = True,
    fresh: bool = True,
    last_task: str | None = None,
) -> list[object]:
    """Build the centered wordmark, workspace details, and quick-start hints."""
    del provider, branch
    width = max(12, width)
    model_name = model.rsplit("/", 1)[-1] if model else "not configured"
    workspace_name = _workspace_name(workspace)
    full_layout = fresh and width >= 70 and height >= 22

    if width >= LOGO_WIDTH + 4:
        wordmark = [Align.center(line, width=width) for line in logo_lines(unicode=unicode)]
    else:
        wordmark = [Align.center(small_wordmark(), width=width)]

    parts: list[object] = [*wordmark]
    parts.append(Align.center(Text("AUTONOMOUS INTELLIGENCE", style="muted"), width=width))

    if not full_layout:
        parts.extend((
            Text(),
            Align.center(_metadata(model_name, workspace_name, width), width=width),
        ))
        if fresh and height >= 16:
            parts.extend((Text(), Align.center(Text("What are we building today?", style="primary"), width=width)))
        return parts

    parts.extend((
        Align.center(
            Text("Your workspace. Your agent. Your next build.", style="primary"),
            width=width,
        ),
        Rule(style="border", characters="─" if unicode else "-"),
        Text(),
        Align.center(_metadata(model_name, workspace_name, width, session_id), width=width),
        Rule(style="border", characters="─" if unicode else "-"),
        Text(),
        Align.center(Text("What are we building today?", style="primary"), width=width),
        Align.center(
            Text("Describe your task. KRYTH will plan, execute, and verify.", style="muted"),
            width=width,
        ),
        Text(),
        Align.center(_command_hints(), width=width),
    ))
    return parts


def welcome_screen(**kwargs: object) -> Group:
    return Group(*welcome_parts(**kwargs))


def _workspace_name(workspace: str | None) -> str:
    if not workspace:
        return "."
    return PureWindowsPath(workspace.rstrip("\\/")).name or workspace


def _metadata(model: str, workspace: str, width: int, session_id: str | None = None) -> Table:
    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(width=9, justify="right", no_wrap=True)
    value_width = max(4, width - 14)
    table.add_column(no_wrap=True, max_width=value_width)
    rows = [("MODEL", _clip(model, value_width)), ("WORKSPACE", _clip(workspace, value_width))]
    if session_id is not None:
        rows.extend((
            ("SESSION", _clip(session_id[:8] or "new", value_width)),
            ("VERSION", KRYTH_VERSION),
        ))
    for label, value in rows:
        table.add_row(
            Text(label, style=PALETTE["muted"]),
            Text(value, style=PALETTE["primary"]),
        )
    return table


def _command_hints() -> Table:
    hints = Table.grid(padding=(0, 3), expand=False)
    hints.add_column(justify="right", no_wrap=True)
    hints.add_column(no_wrap=True)
    for command, description in (
        ("/help", "Explore commands"),
        ("/model", "Choose your model"),
        ("/sessions", "Continue previous work"),
        ("/config", "Configure KRYTH"),
    ):
        hints.add_row(
            Text(command, style=f"bold {PALETTE['accent']}"),
            Text(description, style=PALETTE["muted"]),
        )
    return hints


def _clip(value: str, width: int) -> str:
    width = max(1, width)
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3].rstrip() + "..."
