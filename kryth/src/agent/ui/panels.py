"""KRYTH file update panels."""

from __future__ import annotations

from typing import Sequence

import rich.box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from agent.ui.diff_renderer import ParsedDiff, render_diff
from agent.ui.syntax import highlight_block, lexer_for_path
from agent.ui.theme import CORE, DIVIDER, DOT, ERROR


def _file_title(kind: str, path: str, *, kind_style: str = "accent") -> Text:
    return Text.assemble((CORE, "kryth.core"), ("  " + kind.upper() + "  ", f"bold {kind_style}"), (path, "title"))


def _change_summary(added: int, removed: int) -> Text:
    out = Text()
    out.append(CORE + "  ", style="kryth.core")
    out.append(f"+{added}", style="log.success")
    out.append("  ")
    out.append(f"-{removed}", style="log.error")
    out.append(" lines", style="muted")
    return out


def _byte_summary(content: str) -> Text:
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return Text.assemble((CORE, "kryth.core"), ("  "), (f"{lines:,} lines", "log.success"), ("  " + DOT + "  ", "muted"), (f"{len(content):,} chars", "muted"))


def _wrap(title: Text, body: RenderableType, *, border: str = "divider") -> Panel:
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=border,
        padding=(1, 2),
        expand=False,
        box=rich.box.ROUNDED,
    )


def update_panel(path: str, diff: ParsedDiff, *, body: RenderableType | None = None, extra_header: Text | None = None) -> Panel:
    if body is None:
        body = render_diff(diff, lexer=lexer_for_path(path))
    header = _change_summary(diff.added, diff.removed)
    if extra_header is not None:
        header = Text.assemble(header, "  ", extra_header)
    return _wrap(_file_title("patch", path), Group(header, Text(""), body))


def create_panel(path: str, content: str) -> Panel:
    lexer = lexer_for_path(path)
    lines = content.splitlines() or [""]
    marker: Text | None = None
    max_lines = 40
    if len(lines) > max_lines:
        head_n = max_lines - 8
        tail_n = 8
        shown = "\n".join(lines[:head_n] + ["..."] + lines[-tail_n:])
        marker = Text(f"{CORE}  showing {head_n + tail_n} of {len(lines)} lines", style="muted")
    else:
        shown = "\n".join(lines)
    parts: list[RenderableType] = [_byte_summary(content), Text(""), highlight_block(shown, lexer)]
    if marker is not None:
        parts.extend([Text(""), marker])
    return _wrap(_file_title("new file", path, kind_style="log.success"), Group(*parts), border="log.success")


def delete_panel(path: str) -> Panel:
    return _wrap(
        _file_title("deleted", path, kind_style="log.error"),
        Text.assemble((ERROR, "log.error"), ("  file removed", "log.error")),
        border="log.error",
    )


def multi_update_panel(path: str, diff: ParsedDiff, *, edit_count: int) -> Panel:
    badge = Text(f"{edit_count} edits", style="kbd")
    return update_panel(path, diff, extra_header=badge)


def grouped_updates_panel(title_text: str, children: Sequence[Panel]) -> Panel:
    parts: list[RenderableType] = []
    for i, child in enumerate(children):
        if i:
            parts.append(Rule(style="divider", characters=DIVIDER))
        parts.append(child)
    return Panel(
        Group(*parts),
        title=Text.assemble((CORE, "kryth.core"), ("  " + title_text, "section.exec")),
        title_align="left",
        border_style="divider",
        padding=(1, 1),
        expand=False,
        box=rich.box.ROUNDED,
    )


__all__ = ["update_panel", "create_panel", "delete_panel", "multi_update_panel", "grouped_updates_panel"]
