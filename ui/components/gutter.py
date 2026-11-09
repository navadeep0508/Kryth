from typing import Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.segment import Segment

GUTTER_CHAR = "│"


class Gutter:

    def __init__(self, renderable: Any, style: str = "border") -> None:
        self.renderable = renderable
        self.style = style

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        bar = Segment(f"{GUTTER_CHAR} ", console.get_style(self.style))
        body_width = max(options.max_width - 2, 1)
        lines = console.render_lines(
            self.renderable,
            options.update(width=body_width),
            pad=False,
        )
        for line in lines:
            yield bar
            yield from line
            yield Segment("\n")
