from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text

from ui.format import diff_stat_text
from ui.theme import KRYTH_SYNTAX


def diff_viewer(
    diff: str,
    path: str | None = None,
    *,
    max_lines: int | None = None,
) -> Any:
    """Render a labeled, syntax-highlighted unified diff without a frame."""

    lines = diff.splitlines()
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… {len(lines) - max_lines} more lines"]

    title = Text(path or "diff", style="highlight")
    title.append("  ")
    title.append_text(diff_stat_text(diff))
    return Group(
        title,
        Syntax(
            "\n".join(lines).strip(),
            "diff",
            theme=KRYTH_SYNTAX,
            word_wrap=True,
            background_color="default",
        ),
    )
