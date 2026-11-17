from typing import Any

from rich.text import Text


def usage_line(
    usage: dict[str, Any],
    context_window: int,
    turn_usage: dict[str, Any] | None = None,
) -> Text:
    reported_prompt_tokens = usage.get("prompt_tokens", 0) or 0
    invalid_context_usage = bool(context_window and reported_prompt_tokens > context_window)
    prompt_tokens = reported_prompt_tokens if not invalid_context_usage else 0
    completion_tokens = usage.get("completion_tokens", 0) or 0
    cached_tokens = usage.get("cached_tokens", 0) or 0
    reasoning_tokens = usage.get("reasoning_tokens", 0) or 0

    turn_total = 0
    if turn_usage:
        turn_total = turn_usage.get("total_tokens", 0) or 0
    elif usage:
        turn_total = usage.get("total_tokens", 0) or 0

    line = Text()
    if invalid_context_usage:
        line.append("context overflow", style="error")
        line.append(f" ({reported_prompt_tokens:,} / {context_window:,} over limit)", style="muted")
        line.append(f"   ·   turn: {turn_total:,}", style="muted")
    elif context_window:
        line.append("context ", style="muted")
        line.append(f"{prompt_tokens:,}", style="subtitle")
        line.append(f" / {context_window:,}", style="muted")
        line.append(f"  ({prompt_tokens / context_window * 100:.1f}%)", style="info")
        line.append(f"   ·   turn: {turn_total:,}", style="muted")

    if completion_tokens:
        line.append(f"   ·   {completion_tokens:,} out", style="muted")
    if reasoning_tokens:
        line.append(f"   ·   {reasoning_tokens:,} thinking", style="muted")
    if cached_tokens:
        line.append(f"   ·   {cached_tokens:,} cached", style="muted")

    return line
