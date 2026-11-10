from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.box import ROUNDED
from rich.console import Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from tools.base import ToolConfirmation
from ui.format import diff_counts, edit_preview_rows, guess_language
from ui.theme import KRYTH_SYNTAX
from utils.paths import display_path_relative_to_cwd


def permission_prompt(
    confirmation: ToolConfirmation, cwd: Path | str | None = None
) -> list[Any]:
    """Build the approval prompt for a tool requiring user permission."""

    if confirmation.diff is not None and confirmation.tool_name in {"write", "edit"}:
        return _file_permission_prompt(confirmation, cwd)

    from ui.components.confirmation import confirmation_body

    border = "error" if confirmation.is_dangerous else "warning"
    title = Text.assemble(
        ("⏵ ", border),
        (confirmation.tool_name, "highlight"),
        ("  permission required", "subtitle"),
    )
    if confirmation.is_dangerous:
        title.append("  (dangerous)", style="error")

    description = confirmation.description
    if cwd:
        description = description.replace(f"{cwd}/", "")

    body: Any = confirmation_body(confirmation, cwd)
    if confirmation.affected_paths:
        paths = Text("Affects: ", style="muted")
        paths.append(
            ", ".join(
                display_path_relative_to_cwd(str(path), Path(cwd) if cwd else None)
                for path in confirmation.affected_paths
            ),
            style="code",
        )
        body = Group(body, paths)

    return [
        title,
        Text(description, style="muted"),
        Panel(body, box=ROUNDED, border_style=border, padding=(0, 1)),
    ]


def _file_permission_prompt(
    confirmation: ToolConfirmation, cwd: Path | str | None
) -> list[Any]:
    diff = confirmation.diff.create_diff()
    added, removed = diff_counts(diff)
    path = display_path_relative_to_cwd(
        str(confirmation.diff.path), Path(cwd) if cwd else None
    )
    label = "Update" if confirmation.tool_name == "edit" else "Write"
    header = Text.assemble(
        ("● ", "success"),
        (f"{label}({path})", "tool"),
    )

    if confirmation.diff.is_new_file:
        line_count = len(confirmation.diff.new_content.splitlines())
        summary = Text(f"└  Creates file · {line_count} lines", style="muted")
    elif added and not removed:
        summary = Text(f"└  Adds {added} lines", style="muted")
    elif removed and not added:
        summary = Text(f"└  Removes {removed} lines", style="muted")
    else:
        summary = Text(f"└  Changes +{added} / -{removed} lines", style="muted")

    blocks: list[Any] = [header, summary]
    language = guess_language(path)
    if confirmation.diff.is_new_file:
        code_lines = confirmation.diff.new_content.splitlines()
        visible = code_lines[:10]
        if visible:
            blocks.append(Syntax(
                "\n".join(visible), language, theme=KRYTH_SYNTAX,
                line_numbers=True, start_line=1, word_wrap=False,
                background_color="default",
            ))
        hidden = len(code_lines) - len(visible)
    else:
        preview, hidden = edit_preview_rows(diff, language)
        blocks.extend(preview)

    if hidden:
        blocks.append(Text(f"   … +{hidden} lines", style="muted"))
    return [Group(*blocks)]


def permission_choices(badge: str) -> Any:
    """Render the keyboard hints below a permission prompt."""

    from ui.components.confirmation import confirmation_choices

    return confirmation_choices(badge)
