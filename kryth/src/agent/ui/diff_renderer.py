"""KRYTH unified diff parsing and rendering."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from agent.ui.syntax import highlight_line
from agent.ui.theme import CORE


_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class DiffLine:
    kind: str
    old_no: int | None
    new_no: int | None
    text: str


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: list[DiffLine] = field(default_factory=list)


@dataclass
class ParsedDiff:
    hunks: list[Hunk] = field(default_factory=list)
    added: int = 0
    removed: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.hunks


def parse_unified_diff(text: str) -> ParsedDiff:
    out = ParsedDiff()
    current: Hunk | None = None
    old_no = new_no = 0
    for raw in text.splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        m = _HUNK_HEADER.match(raw)
        if m:
            current = Hunk(
                old_start=int(m.group(1)),
                old_count=int(m.group(2) or 1),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) or 1),
                header=(m.group(5) or "").strip(),
            )
            out.hunks.append(current)
            old_no = current.old_start
            new_no = current.new_start
            continue
        if current is None:
            continue
        if not raw:
            current.lines.append(DiffLine("ctx", old_no, new_no, ""))
            old_no += 1
            new_no += 1
            continue
        marker, body = raw[0], raw[1:]
        if marker == "+":
            current.lines.append(DiffLine("add", None, new_no, body))
            new_no += 1
            out.added += 1
        elif marker == "-":
            current.lines.append(DiffLine("del", old_no, None, body))
            old_no += 1
            out.removed += 1
        else:
            current.lines.append(DiffLine("ctx", old_no, new_no, body))
            old_no += 1
            new_no += 1
    return out


def _gutter_text(n: int | None) -> Text:
    return Text("·" if n is None else str(n), style="diff.gutter")


def _line_row(line: DiffLine, lexer: str) -> tuple[Text, Text, Text]:
    body = Text(no_wrap=False, overflow="fold")
    if line.kind == "add":
        body.append("+ ", style="diff.add bold")
        body.append_text(highlight_line(line.text, lexer))
    elif line.kind == "del":
        body.append("- ", style="diff.del bold")
        body.append_text(highlight_line(line.text, lexer))
    else:
        body.append("  ", style="muted")
        body.append_text(highlight_line(line.text, lexer))
    return _gutter_text(line.old_no), _gutter_text(line.new_no), body


def _collapse(lines: list[DiffLine], max_lines: int) -> Iterable[DiffLine | str]:
    if len(lines) <= max_lines:
        yield from lines
        return
    half = max(2, max_lines // 2)
    head, tail = lines[:half], lines[-half:]
    yield from head
    yield f"{CORE} {len(lines) - len(head) - len(tail)} lines hidden"
    yield from tail


def _hunk_header_row(hunk: Hunk) -> tuple[Text, Text, Text]:
    body = Text.assemble((CORE, "kryth.core"), (" line ", "muted"), (str(hunk.new_start), "diff.hunk"))
    if hunk.header:
        body.append("  ")
        body.append(hunk.header, style="hint")
    return Text(""), Text(""), body


def _divider_row(message: str) -> tuple[Text, Text, Text]:
    return Text(""), Text(""), Text("  " + message, style="muted")


def render_diff(parsed: ParsedDiff, *, lexer: str = "text", max_hunk_lines: int = 24, max_hunks: int = 6) -> RenderableType:
    grid = Table.grid(padding=(0, 1), expand=False)
    grid.add_column(style="diff.gutter", justify="right", no_wrap=True, min_width=4)
    grid.add_column(style="diff.gutter", justify="right", no_wrap=True, min_width=4)
    grid.add_column(overflow="fold")

    hunks = parsed.hunks
    skipped = 0
    if len(hunks) > max_hunks:
        head = hunks[: max_hunks - 2]
        tail = hunks[-2:]
        skipped = len(hunks) - len(head) - len(tail)
        hunks_to_render = list(head) + [None] + list(tail)  # type: ignore[list-item]
    else:
        hunks_to_render = list(hunks)

    first = True
    for hunk in hunks_to_render:
        if hunk is None:
            grid.add_row(*_divider_row(f"{CORE} {skipped} hunks hidden"))
            first = True
            continue
        if not first:
            grid.add_row(Text(""), Text(""), Text(""))
        first = False
        grid.add_row(*_hunk_header_row(hunk))
        for item in _collapse(hunk.lines, max_hunk_lines):
            if isinstance(item, str):
                grid.add_row(*_divider_row(item))
            else:
                grid.add_row(*_line_row(item, lexer))
    return grid


def render_diff_text(diff_text: str, *, lexer: str = "text", max_hunk_lines: int = 24, max_hunks: int = 6) -> tuple[RenderableType, ParsedDiff]:
    parsed = parse_unified_diff(diff_text)
    return render_diff(parsed, lexer=lexer, max_hunk_lines=max_hunk_lines, max_hunks=max_hunks), parsed


__all__ = ["DiffLine", "Hunk", "ParsedDiff", "parse_unified_diff", "render_diff", "render_diff_text"]
