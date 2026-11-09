from __future__ import annotations

from rich.text import Text

from ui.components.shimmer import shimmer_tool_label, shimmers


def tool_progress_panel(
    name: str,
    elapsed: int,
    progress: str = "",
    *,
    tool_kind: str | None = None,
    headline: str | None = None,
    spinner: str = ".",
    frame: int = 0,
) -> Text:
    """Render a quiet single-line status while a tool is running."""

    line = Text.assemble((f"{spinner} ", "tool"))
    if shimmers(tool_kind):
        line.append_text(shimmer_tool_label(name, frame))
    else:
        line.append(name.replace("_", " ").capitalize(), style="highlight")
    if headline:
        line.append("  ")
        line.append(headline, style="subtitle")
    line.append(f" {elapsed}s", style="muted")
    if progress:
        line.append(" > ", style="dim")
        line.append(progress, style="muted")

    return line
