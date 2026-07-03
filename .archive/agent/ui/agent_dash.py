"""Agent Dashboard — Multi-agent team visualization.

Renders when AGENT_UPDATE events arrive:

    Engineering Team

    Planner      ██████████  100%  idle
    Backend      ███████░░░   70%  Building API routes
    Frontend     ████████░░   82%  Generating components
    Testing      █████░░░░░   51%  Running test suite
    Review       ██░░░░░░░░   20%  Code review
"""

from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


def _bar(percent: int, width: int = 16) -> Text:
    filled = round(percent / 100 * width)
    empty = width - filled
    t = Text()
    t.append("█" * filled, style="agent.bar")
    t.append("░" * empty, style="agent.bar.bg")
    return t


def _status_style(status: str) -> str:
    return {
        "running": "agent.running",
        "done": "agent.done",
        "failed": "agent.failed",
        "idle": "agent.idle",
    }.get(status, "muted")


def _status_glyph(status: str) -> str:
    return {
        "running": CORE,
        "done": CORE,
        "failed": ERROR,
        "idle": WAITING,
    }.get(status, WAITING)


def render_agent_dashboard(agents: list) -> None:
    """Render the multi-agent team dashboard."""
    if not agents:
        return

    body = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=14, style="agent.name")
    body.add_column(no_wrap=True, min_width=20)
    body.add_column(no_wrap=True, min_width=6, style="muted")
    body.add_column(overflow="fold", style="muted")

    for agent in agents:
        name = agent.name if hasattr(agent, "name") else agent.get("name", "?")
        status = agent.status if hasattr(agent, "status") else agent.get("status", "idle")
        progress = agent.progress if hasattr(agent, "progress") else agent.get("progress", 0)
        task = agent.task if hasattr(agent, "task") else agent.get("task", "")

        style = _status_style(status)
        glyph = _status_glyph(status)

        name_text = Text.assemble(
            (glyph, style), ("  ", ""), (name, style)
        )
        bar = _bar(progress)
        pct = Text(f"{progress}%", style=style)
        task_text = Text(task[:40], style="muted") if task else Text("")

        body.add_row(name_text, bar, pct, task_text)

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Engineering Team", "eng.section")),
        title_align="left",
        border_style="divider",
        padding=(0, 2),
        expand=False,
    ))


def render_agent_line(name: str, status: str, task: str, progress: int) -> None:
    """Render a single agent status line (inline update mode)."""
    style = _status_style(status)
    glyph = _status_glyph(status)
    bar = _bar(progress, width=12)

    line = Text.assemble(
        (glyph, style),
        (f"  {name:<12}", style),
        ("  ", ""),
    )
    line.append_text(bar)
    line.append(f"  {progress}%", style=style)
    if task:
        line.append(f"  {DOT}  {task[:50]}", style="muted")

    console.print(line)
