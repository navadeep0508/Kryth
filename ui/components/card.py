"""Bordered card containers for the KRYTH terminal interface.

Cards are the base container for grouped information. They keep backgrounds
terminal-owned and carry signal through border style, title and content
colour instead.
"""

from __future__ import annotations

from typing import Any

from rich.box import ROUNDED, Box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text


def card(
    title: str | Text | None = None,
    *blocks: Any,
    border_style: str = "border",
    padding: tuple[int, int] = (0, 1),
    box: Box = ROUNDED,
) -> Panel:
    """Wrap blocks in a rounded, bordered card with an optional title.

    `border_style` is a theme style name (see `ui/theme/styles.py`), e.g.
    "border", "warning", "error", "success" or "primary".
    """
    body = Group(*blocks) if blocks else Group()
    return Panel(
        body,
        title=title,
        border_style=border_style,
        box=box,
        padding=padding,
    )
