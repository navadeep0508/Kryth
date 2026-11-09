from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from ui.components.gutter import Gutter
from ui.components.markdown import render_inline
from ui.components.tool_call import TOOL_ICON

TRANSCRIPT_MESSAGES = 12
TRANSCRIPT_LINES = 6

PROMPT_MARK = ">"


def echo_user_message(content: str) -> Text:
    return Text.assemble((f"{PROMPT_MARK} ", "info"), (content, "user"))


def _transcript_body(content: str, max_lines: int = TRANSCRIPT_LINES) -> str:
    lines = [line for line in content.strip().splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    hidden = len(lines) - max_lines
    return "\n".join(lines[:max_lines] + [f"… {hidden} more line{'s' if hidden > 1 else ''}"])


def render_transcript(
    console: Console,
    messages: list[Any],
    limit: int = TRANSCRIPT_MESSAGES,
) -> None:
    """Replay a restored conversation, compactly.

    Tool results are left out: the model still has them, but they are the
    bulkiest and least useful part to read back.
    """

    shown = [message for message in messages if message.role in {"user", "assistant"}]
    if not shown:
        return

    omitted = max(0, len(shown) - limit)
    shown = shown[len(shown) - limit:] if omitted else shown

    console.print()
    if omitted:
        console.print(
            Text(f"… {omitted} earlier message{'s' if omitted > 1 else ''} not shown", style="dim")
        )

    for message in shown:
        content = (message.content or "").strip()

        if message.role == "user":
            if content:
                console.print()
                console.print(echo_user_message(_transcript_body(content)))
            continue

        if content:
            console.print(
                Gutter(render_inline(_transcript_body(content), base_style="assistant"))
            )

        for tool_call in message.tool_calls or []:
            name = tool_call.get("function", {}).get("name", "tool")
            console.print(Text(f"  {TOOL_ICON} {name}", style="dim"))
