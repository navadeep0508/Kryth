from __future__ import annotations

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.styles import Style

from ui.components import PROMPT_MARK, status_bar
from ui.theme import hex_colour

PROMPT_WIDTH = 4  # "| > " inside the responsive composer frame
COMPOSER_MIN_WIDTH = 60

PROMPT_STYLE = Style.from_dict(
    {
        "prompt": f"{hex_colour('accent')} bold",
        "frame": hex_colour("graphite"),
        "bottom-toolbar": "noreverse",
        "bottom-toolbar.text": f"noreverse {hex_colour('silver')}",
        "approval": f"noreverse {hex_colour('silver')}",
        "approval.warn": f"noreverse {hex_colour('amber')}",
        "approval.danger": f"noreverse bold {hex_colour('red')}",
        "status": f"noreverse {hex_colour('silver')}",
        "status.warn": f"noreverse {hex_colour('amber')}",
        "status.danger": f"noreverse bold {hex_colour('red')}",
        "completion-menu": "",
        "completion-menu.completion": f"{hex_colour('bright')}",
        "completion-menu.completion.current": f"{hex_colour('accent')} bold",
        "completion-menu.meta.completion": f"{hex_colour('silver')}",
        "completion-menu.meta.completion.current": f"{hex_colour('silver')}",
    }
)

APPROVAL_RISK_CLASSES = {
    "normal": "class:approval",
    "warn": "class:approval.warn",
    "danger": "class:approval.danger",
}


def soft_wrap(text: str, width: int) -> str:
    """Compatibility helper for callers that need word-boundary wrapping."""
    if width < 1:
        return text
    chars = list(text)
    line_start = 0
    last_space = -1
    i = 0
    while i < len(chars):
        if chars[i] == " ":
            last_space = i
        if i - line_start + 1 > width and last_space > line_start:
            chars[last_space] = "\n"
            line_start = last_space + 1
            last_space = -1
            i = line_start
            continue
        i += 1
    return "".join(chars)


def status_readout(
    model_name: str,
    context_ratio: float | None,
    width: int,
    approval_label: str = "",
    cwd: str | None = None,
    provider: str | None = None,
    agent_state: str | None = None,
    turn_tokens: int | None = None,
    git_branch: str | None = None,
) -> tuple[str, str] | None:
    return status_bar(
        model_name,
        approval_label,
        context_ratio,
        cwd,
        width,
        provider=provider,
        agent_state=agent_state,
        turn_tokens=turn_tokens,
        git_branch=git_branch,
    )


def prompt_fragments(
    width: int,
    expansion: StyleAndTextTuples | None = None,
    placeholder: bool = False,
) -> StyleAndTextTuples:
    parts = [*(expansion or [])]
    if width >= COMPOSER_MIN_WIDTH:
        actions = "KRYTH  |  Enter sends  |  / commands"
        if width >= 78:
            actions = "KRYTH  |  Enter sends  |  Ctrl+J new line  |  / commands"
        parts.append(("class:frame", _frame_line(width, actions) + "\n"))
        parts.extend([("class:frame", "| "), ("class:prompt", f"{PROMPT_MARK} ")])
        if placeholder:
            hint = "Ask KRYTH to build, debug, or improve..."
            parts.append(("class:status", hint[: max(0, width - PROMPT_WIDTH - 2)]))
    else:
        parts.append(("class:prompt", f"{PROMPT_MARK} "))
        if placeholder and width > PROMPT_WIDTH + 4:
            hint = "Ask KRYTH to build, debug, or improve..."
            parts.append(("class:status", hint[: width - PROMPT_WIDTH]))
    return parts


def continuation_fragments(width: int) -> StyleAndTextTuples:
    if width >= COMPOSER_MIN_WIDTH:
        return [("class:frame", "|   ")]
    return [("", " " * (len(PROMPT_MARK) + 1))]


def bottom_fragments(
    width: int,
    badge: str,
    risk: str,
    readout: tuple[str, str] | None = None,
) -> StyleAndTextTuples:
    if readout is not None:
        style, text = readout
        return [(f"class:{style}", f" {text[:max(0, width - 1)]}")]
    return [(APPROVAL_RISK_CLASSES.get(risk, "class:approval"), f" {badge.strip()}")]


def _frame_line(width: int, label: str) -> str:
    """Fit an ASCII rule and its label to the available terminal columns."""
    width = max(2, width)
    content = f"-- {label} "
    content = content[: width - 2]
    return "+" + content + "-" * max(0, width - len(content) - 2) + "+"
