"""Quiet, one-line notifications for the terminal interface."""

from rich.text import Text


def notification(
    message: str,
    *,
    label: str = "Notice",
    tone: str = "muted",
    width: int | None = None,
) -> Text:
    line = Text(label, style=tone)
    line.append("  ")
    available = max(0, width - len(label) - 2) if width is not None else len(message)
    message = message if width is None else _clip(message, available)
    line.append(message, style="muted")
    return line


def _clip(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."
