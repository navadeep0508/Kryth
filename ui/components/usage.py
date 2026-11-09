from typing import Any

from rich.text import Text


def usage_line(usage: dict[str, Any], context_window: int) -> Text:
    prompt_tokens = usage.get("prompt_tokens", 0) or 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    cached_tokens = usage.get("cached_tokens", 0) or 0
    reasoning_tokens = usage.get("reasoning_tokens", 0) or 0

    line = Text()
    line.append("context ", style="muted")
    line.append(f"{prompt_tokens:,}", style="subtitle")
    line.append(f" / {context_window:,}", style="muted")

    if context_window:
        line.append(f"  ({prompt_tokens / context_window * 100:.1f}%)", style="info")

    line.append("   ·   ", style="muted")
    line.append(f"{completion_tokens:,} out", style="muted")
    if reasoning_tokens:
        line.append("   ·   ", style="muted")
        line.append(f"{reasoning_tokens:,} thinking", style="muted")
    if cached_tokens:
        line.append("   ·   ", style="muted")
        line.append(f"{cached_tokens:,} cached", style="muted")

    return line
