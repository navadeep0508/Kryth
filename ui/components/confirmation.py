from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.box import ROUNDED
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tools.base import ToolConfirmation
from ui.components.args_table import render_args_table
from ui.components.diff_viewer import diff_viewer
from ui.components.permission_prompt import permission_prompt
from ui.components.terminal_approval import TerminalApprovalPanel
from utils.paths import display_path_relative_to_cwd

MAX_CONFIRM_DIFF_LINES = 20

APPROVE_KEYS = {"y", "a"}
REJECT_KEYS = {"n", "d", "q"}


def confirmation_body(
    confirmation: ToolConfirmation, cwd: Path | str | None = None
) -> Group:
    blocks: list[Any] = []

    if confirmation.command:
        blocks.append(Text(f"$ {confirmation.command}", style="code"))

    if confirmation.diff is not None:
        # The file header is redundant here: the path is already in the title.
        diff = "\n".join(
            line
            for line in confirmation.diff.create_diff().splitlines()
            if not line.startswith(("--- ", "+++ "))
        ).strip()
        if diff:
            blocks.append(
                diff_viewer(
                    diff,
                    display_path_relative_to_cwd(
                        str(confirmation.diff.path), Path(cwd) if cwd else None
                    ),
                    max_lines=MAX_CONFIRM_DIFF_LINES,
                )
            )

    if not blocks:
        blocks.append(render_args_table(confirmation.tool_name, confirmation.params))

    return Group(*blocks)


def confirmation_request(
    confirmation: ToolConfirmation, cwd: Path | str | None = None
) -> list[Any]:
    """Build the approval prompt for a tool requiring user permission.

    Terminal (bash) commands use the rich TerminalApprovalPanel with risk
    classification, shell detection, and command preview. All other tools
    use the classic permission prompt.
    """

    # Terminal commands get the new rich approval panel.
    if confirmation.tool_name == "bash":
        return [TerminalApprovalPanel(confirmation, cwd)]

    return permission_prompt(confirmation, cwd)


def confirmation_choices(badge: str) -> Panel:
    """Render approval shortcuts as a compact, keyboard-focused choice card."""

    def choice(key: str, label: str, key_style: str) -> Text:
        return Text.assemble((f"[{key}] ", key_style), (label, "primary"))

    options = Table.grid(padding=(0, 3))
    options.add_column(no_wrap=True)
    options.add_column(no_wrap=True)
    options.add_row(
        choice("Y", "Allow once", "success"),
        choice("A", "Allow for session", "info"),
    )
    options.add_row(
        choice("N", "Reject", "error"),
        choice("Esc", "Cancel", "subtitle"),
    )

    return Panel(
        Group(options, Text(badge, style="muted")),
        title=Text("Approval", style="warning"),
        border_style="warning",
        box=ROUNDED,
        padding=(0, 1),
    )
