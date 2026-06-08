"""KRYTH Layout Manager — centralized layout control."""

from __future__ import annotations

from typing import Literal

from rich.console import Console
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from .console import console as default_console
from .theme import (
    CORE,
    LEFT_MARGIN,
    MAX_PANEL_WIDTH,
    PANEL_MARGIN,
    SECTION_MARGIN,
)


class LayoutManager:
    """Manages consistent layout across all UI elements."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or default_console
        self._left_margin = LEFT_MARGIN
        self._section_margin = SECTION_MARGIN
        self._panel_margin = PANEL_MARGIN
        self._max_panel_width = MAX_PANEL_WIDTH

    def panel_width(self, custom_width: int | None = None) -> int:
        """Calculate optimal panel width."""
        width = custom_width or self._max_panel_width
        try:
            terminal_width = self.console.width
            available = terminal_width - self._left_margin - 2
            return min(width, max(40, available))
        except Exception:
            return width

    def left_padding(self) -> int:
        """Get left margin for all elements."""
        return self._left_margin

    def section_spacing(self) -> int:
        """Get vertical spacing between sections."""
        return self._section_margin

    def panel_padding(self) -> int:
        """Get internal panel padding."""
        return self._panel_margin

    def apply_margin(self, renderable, margin: int | None = None) -> Padding:
        """Apply left margin to any renderable."""
        return Padding(renderable, (0, 0, 0, margin or self._left_margin))

    def create_panel(
        self,
        title: str,
        content,
        *,
        border_style: str = "divider",
        title_align: Literal["left", "center", "right"] = "left",
        padding: tuple[int, int] | None = None,
        width: int | None = None,
        center: bool = False,
    ) -> Panel:
        """Create a consistently styled panel with proper margins."""
        panel_width = self.panel_width(width)

        if padding is None:
            padding = (1, self._panel_margin)

        # Build title with KRYTH core symbol
        title_text = Text.assemble((CORE, "kryth.core"), ("  " + title, "bold"))

        panel = Panel(
            content,
            title=title_text,
            title_align=title_align,
            border_style=border_style,
            padding=padding,
            width=panel_width,
            expand=False,
        )

        # Apply left margin unless centering
        if not center:
            return self.apply_margin(panel, self._left_margin)
        return panel

    def create_centered_panel(
        self,
        title: str,
        content,
        *,
        border_style: str = "divider",
        padding: tuple[int, int] | None = None,
    ) -> Panel:
        """Create a panel centered on screen (for important messages)."""
        return self.create_panel(
            title,
            content,
            border_style=border_style,
            center=True,
            padding=padding,
        )

    def vertical_spacer(self) -> Padding:
        """Create vertical spacing between sections."""
        return Padding("", (self._section_margin, 0, 0, 0))


# Global layout manager instance
_layout = LayoutManager()


def get_layout() -> LayoutManager:
    """Get the global layout manager."""
    return _layout