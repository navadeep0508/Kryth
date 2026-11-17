from __future__ import annotations

import re
from typing import Any, Callable

from rich.console import Group
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from ui.theme import KRYTH_SYNTAX
from ui.theme.palette import hex_colour

_INLINE = re.compile(
    r"(?P<code>`[^`\n]+`)"
    r"|(?P<bolditalic>\*\*\*(?!\s)[^\n]+?\*\*\*)"
    r"|(?P<bold>\*\*(?!\s)[^\n]+?\*\*|__(?!\s)[^\n]+?__)"
    r"|(?P<strike>~~(?!\s)[^\n]+?~~)"
    r"|(?P<italic>\*(?!\s)[^*\n]+?\*|(?<![A-Za-z0-9_])_(?!\s)[^_\n]+?_(?![A-Za-z0-9_]))"
    r"|(?P<link>\[(?P<label>[^\]\n]*)\]\((?P<url>[^)\s]*)[^)\n]*\))"
)

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_RULE = re.compile(r"^\s{0,3}(?:-{3,}|_{3,}|\*{3,})\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*([\w+#.-]*)")

BULLET_CHAR = "-"

_HEADING_STYLES = {1: "md.h1", 2: "md.h2"}

_MARKER_WIDTHS = {"code": 1, "bolditalic": 3, "bold": 2, "strike": 2, "italic": 1}
_TABLE_DELIMITER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_TABLE_ROW = re.compile(r"^\s*\|?.+\|.+\|?\s*$")


def render_inline(text: str, base_style: str = "assistant") -> Text:
    rendered = Text(style=base_style)
    cursor = 0

    for match in _INLINE.finditer(text):
        if match.start() > cursor:
            rendered.append(text[cursor:match.start()])

        kind = match.lastgroup
        if match.group("link") is not None:
            label = match.group("label") or match.group("url")
            rendered.append(label, style="md.link")
            if match.group("url"):
                rendered.append(f" ({match.group('url')})", style="muted")
        else:
            width = _MARKER_WIDTHS[kind]
            body = match.group(kind)[width:-width]
            rendered.append(body, style=f"md.{kind}")

        cursor = match.end()

    if cursor < len(text):
        rendered.append(text[cursor:])

    return rendered


def render_line(line: str) -> Any:
    if _RULE.match(line):
        return Rule(style="md.rule")

    heading = _HEADING.match(line)
    if heading:
        level = len(heading.group(1))
        style = _HEADING_STYLES.get(level, "md.h3")
        return render_inline(heading.group(2), base_style=style)

    quote = _QUOTE.match(line)
    if quote:
        body = Text("> ", style="md.quote")
        body.append_text(render_inline(quote.group(1), base_style="md.quote"))
        return body

    ordered = _ORDERED.match(line)
    if ordered:
        indent, number, rest = ordered.groups()
        body = Text(indent)
        body.append(f"{number}. ", style="md.bullet")
        body.append_text(render_inline(rest))
        return body

    bullet = _BULLET.match(line)
    if bullet:
        indent, rest = bullet.groups()
        body = Text(indent)
        body.append(f"{BULLET_CHAR} ", style="md.bullet")
        body.append_text(render_inline(rest))
        return body

    return render_inline(line)


class MarkdownStream:
    def __init__(self, emit: Callable[[Any], None]) -> None:
        self._emit = emit
        self._pending = ""

        self._fence: str | None = None
        self._language = "text"
        self._code: list[str] = []
        self._paragraph: list[str] = []
        self._table: list[str] | None = None

        self._blank = True

    @property
    def in_code_block(self) -> bool:
        return self._fence is not None

    def feed(self, chunk: str) -> None:
        if not chunk:
            return

        self._pending += chunk
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._line(line)

    def close(self) -> None:
        if self._pending:
            self._line(self._pending)
            self._pending = ""

        if self._fence is not None:
            self._flush_code()
        self._flush_table()
        self._flush_paragraph()

    def _line(self, line: str) -> None:
        line = line.rstrip()
        fence = _FENCE.match(line)

        if self._table is not None:
            if _TABLE_ROW.match(line) and "|" in line:
                self._table.append(line)
                return
            self._flush_table()

        if not self._fence and "|" in line and _TABLE_ROW.match(line):
            self._flush_paragraph()
            self._table = [line]
            return

        if self._fence is not None:
            if fence and fence.group(1)[0] == self._fence[0] and len(fence.group(1)) >= len(self._fence):
                self._flush_code()
            else:
                self._code.append(line)
            return

        if fence:
            self._flush_paragraph()
            self._fence = fence.group(1)
            self._language = fence.group(2) or "text"
            self._code = []
            self._gap()
            return

        if not line.strip():
            self._flush_paragraph()
            self._gap()
            return

        if _HEADING.match(line) or _RULE.match(line):
            self._flush_paragraph()
            self._gap()

        if _HEADING.match(line) or _RULE.match(line) or _QUOTE.match(line) or _ORDERED.match(line) or _BULLET.match(line):
            self._write(render_line(line))
        else:
            self._paragraph.append(line.strip())

    def _flush_paragraph(self) -> None:
        if self._paragraph:
            self._write(render_inline(" ".join(self._paragraph)))
            self._paragraph.clear()

    def _flush_table(self) -> None:
        if not self._table:
            self._table = None
            return
        rows = self._table
        self._table = None
        if len(rows) > 1 and _TABLE_DELIMITER.match(rows[1]):
            rows = [rows[0], *rows[2:]]
        parsed = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
        if parsed:
            columns = max(len(row) for row in parsed)
            table = Table.grid(padding=(0, 2))
            for _ in range(columns):
                table.add_column(overflow="fold")
            for row_index, row in enumerate(parsed):
                cells = [render_inline(cell, "md.h2" if row_index == 0 else "assistant") for cell in row]
                table.add_row(*(cells + [Text("")] * (columns - len(cells))))
            self._write(table)

    def _flush_code(self) -> None:
        code = "\n".join(self._code).strip("\n")
        self._fence = None
        self._code = []

        if not code.strip():
            return

        self._write(Text(self._language, style="muted"))
        self._write(Syntax(
            code, self._language, theme=KRYTH_SYNTAX,
            background_color=hex_colour("slate"), word_wrap=True,
        ))
        self._gap()

    def _gap(self) -> None:
        if self._blank:
            return
        self._emit(Text(""))
        self._blank = True

    def _write(self, renderable: Any) -> None:
        self._emit(renderable)
        self._blank = False


def render_markdown(text: str) -> Group:
    parts: list[Any] = []
    stream = MarkdownStream(parts.append)
    stream.feed(text)
    stream.close()
    return Group(*parts)
