"""KRYTH command execution panel."""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from agent.ui.console import console
from agent.ui.panels import _print_panel
from agent.ui.summarizer import OutputSummary
from agent.ui.theme import CORE, DOT, ERROR


def _title_for(command: str, exit_code: int) -> Text:
    short = command if len(command) <= 90 else command[:87] + "..."
    if exit_code == 0:
        return Text.assemble((CORE, "log.success"), (" EXEC ", "section.shell"), (short, "title"))
    return Text.assemble((ERROR, "log.error"), (f" EXEC exit {exit_code} ", "log.error"), (short, "title"))


def _status_line(summary: OutputSummary, exit_code: int) -> Text:
    line = Text()
    if exit_code == 0:
        line.append(CORE + " Complete", style="log.success")
        line.append("  " + DOT + "  exit 0", style="muted")
    else:
        line.append(ERROR + " Error", style="log.error bold")
        line.append(f"  {DOT}  exit {exit_code}", style="log.error")
    if summary.errors:
        line.append(f"  {DOT}  {summary.errors} errors", style="log.error")
    if summary.warnings:
        line.append(f"  {DOT}  {summary.warnings} warnings", style="log.warn")
    if summary.headline:
        line.append(f"  {DOT}  {summary.headline}", style="muted")
    return line


def _body_for(summary: OutputSummary) -> Text:
    body = Text()
    if not summary.first_lines and not summary.last_lines:
        body.append("(no output)", style="muted")
        return body
    for line in summary.first_lines:
        body.append(line + "\n")
    if summary.truncated:
        if summary.first_lines:
            body.append("\n")
        body.append(f"  {CORE} {summary.hidden} lines hidden\n\n", style="muted")
    for i, line in enumerate(summary.last_lines):
        body.append(line + ("\n" if i < len(summary.last_lines) - 1 else ""))
    return body


def render_command_panel(
    *,
    command: str,
    summary: OutputSummary,
    exit_code: int,
    timeout: int,
    note: str | None,
) -> None:
    parts = [_status_line(summary, exit_code), Rule(style="divider"), _body_for(summary)]
    if note:
        parts.extend([Rule(style="divider"), Text(note, style="muted")])
    footer = f"timeout {timeout}s · {summary.total_lines} lines"
    _print_panel(Panel(
        Group(*parts),
        title=_title_for(command, exit_code),
        title_align="left",
        subtitle=footer,
        subtitle_align="right",
        border_style="divider" if exit_code == 0 else "log.error",
        padding=(0, 1),
        expand=True,
    ))
