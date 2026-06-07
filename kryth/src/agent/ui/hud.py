"""KRYTH startup and status renderables."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import rich.box
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.motion import pulse_badge, reveal_lines, status_chip, truncate_middle, DIAMOND_BOOT_FRAMES, KRYTH_ASCII_ART, sleep, motion_enabled
from agent.ui.theme import CHECK, CORE, DOT, ERROR, WAITING


@dataclass(frozen=True)
class CognitionStep:
    label: str
    detail: str = ""
    state: str = "pending"  # pending | active | done | warn | error


_STATE = {
    "pending": (WAITING, "muted"),
    "active": (CORE, "kryth.core"),
    "done": (CHECK, "log.success"),
    "warn": (ERROR, "log.warn"),
    "error": (ERROR, "log.error"),
}


def hero_banner(*, model: str, base_url: str, skill_count: int, version: str, no_key: bool) -> Panel:
    """Full-width session dashboard with organized layout."""

    # Two-column layout for better space utilization
    grid = Table.grid(padding=(0, 3), expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    # Row 1: Model and Tools
    grid.add_row(
        Text.assemble((CORE, "kryth.core"), (" MODEL", "muted"), ("  ", ""), (truncate_middle(model, 45), "title")),
        Text.assemble((CORE, "kryth.core"), (" TOOLS", "muted"), ("  ", ""), (f"{skill_count} loaded", "title"))
    )

    # Row 2: Endpoint and Version
    grid.add_row(
        Text.assemble((CORE, "kryth.core"), (" ENDPOINT", "muted"), ("  ", ""), (truncate_middle(base_url, 45), "title")),
        Text.assemble((CORE, "kryth.core"), (" VERSION", "muted"), ("  ", ""), (version, "title"))
    )

    # Status line - full width
    if no_key:
        status = Text.assemble(
            (ERROR, "log.error"),
            (" API key required", "log.error"),
            ("  " + DOT + "  ", "muted"),
            ("Run /config to configure", "muted")
        )
    else:
        status = Text.assemble(
            (CORE, "log.success"),
            (" Ready", "log.success"),
            ("  " + DOT + "  ", "muted"),
            ("Type your request or /help for commands", "muted")
        )

    # Combine everything
    body = Group(grid, Text(""), status)

    return Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), (" KRYTH", "title")),
        title_align="left",
        border_style="hud.border",
        padding=(1, 2),
        expand=True,
        box=rich.box.ROUNDED,
    )


def _show_boot_animation() -> None:
    """Display the cinematic diamond boot sequence."""
    # Skip animation if motion is disabled or non-tty
    if not motion_enabled():
        return

    # Center the animation based on terminal width
    try:
        term_width = console.size.width
    except Exception:
        term_width = 80

    for frame in DIAMOND_BOOT_FRAMES:
        # Center the frame
        padding = max(0, (term_width - len(frame)) // 2)
        centered = " " * padding + frame
        console.print(centered, style="kryth.core")
        sleep(0.12)  # ~120ms per frame for smooth but fast animation

    console.print()  # blank line after animation


def startup_reveal(*, model: str, base_url: str, skill_count: int, version: str, no_key: bool) -> None:
    """Clean, organized KRYTH startup sequence with full-width layout."""
    console.print()

    # ASCII art header - centered and prominent
    if KRYTH_ASCII_ART:
        for line in KRYTH_ASCII_ART.split("\n"):
            console.print(line, style="kryth.core", justify="center")
        console.print()
        console.print("            ⟨◉_◉⟩", style="kryth.core", justify="center")
        console.print()
        console.print("    Autonomous Coding Intelligence", style="title", justify="center")
        console.print()
        sleep(0.15)

    # Full-width initialization panel
    init_table = Table.grid(padding=(0, 2), expand=True)
    init_table.add_column(ratio=1)
    init_table.add_column(ratio=1)

    init_table.add_row(
        Text.assemble((CORE, "kryth.core"), (" Initializing KRYTH", "muted")),
        Text.assemble((CORE, "kryth.core"), (" Loading configuration", "muted"))
    )
    init_table.add_row(
        Text.assemble((CORE, "kryth.core"), (" Scanning workspace", "muted")),
        Text.assemble((CORE, "kryth.core"), (" Preparing execution engine", "muted"))
    )
    init_table.add_row(
        Text.assemble((CORE, "kryth.core"), (" Loading tools", "muted"), ("  ", "muted"), (f"{skill_count} available", "title")),
        Text.assemble((CORE, "kryth.core"), (" Agent ready", "log.success"))
    )

    console.print(Panel(
        init_table,
        title=Text.assemble((CORE, "kryth.core"), (" System Initialization", "title")),
        title_align="left",
        border_style="hud.border",
        padding=(1, 2),
        expand=True,
        box=rich.box.ROUNDED,
    ))

    console.print()
    console.print(hero_banner(model=model, base_url=base_url, skill_count=skill_count, version=version, no_key=no_key))
    console.print()


def cognition_timeline(steps: Sequence[CognitionStep], *, title: str = "thinking") -> Panel:
    table = Table.grid(padding=(0, 1), expand=False)
    table.add_column(no_wrap=True)
    table.add_column(overflow="fold")
    table.add_column(overflow="fold")

    for step in steps:
        glyph, style = _STATE.get(step.state, _STATE["pending"])
        table.add_row(Text(glyph, style=style), Text(step.label, style=style), Text(step.detail, style="muted"))

    return Panel(
        table,
        title=Text.assemble((CORE, "kryth.core"), (" " + title, "section.plan")),
        title_align="left",
        border_style="divider",
        padding=(1, 2),
        expand=False,
        box=rich.box.ROUNDED,
    )


def execution_graph(items: Iterable[dict]) -> Panel:
    rows = Table.grid(padding=(0, 1), expand=False)
    rows.add_column(no_wrap=True)
    rows.add_column(overflow="fold")
    rows.add_column(no_wrap=True)

    for item in items:
        status = item.get("status", "pending")
        glyph, style = _STATE.get(status, _STATE["pending"])
        rows.add_row(Text(glyph, style=style), Text(str(item.get("text", "operation")), style="title"), Text(str(item.get("meta", "")), style="muted"))

    return Panel(rows, title=Text.assemble((CORE, "kryth.core"), (" execution", "section.exec")), border_style="divider", padding=(1, 2), expand=False, box=rich.box.ROUNDED)


def diagnostic_card(*, title: str, message: str, severity: str = "info", suggestions: Sequence[str] = ()) -> Panel:
    style = {"info": "kryth.core", "warn": "log.warn", "error": "log.error", "success": "log.success"}.get(severity, "kryth.core")
    glyph = ERROR if severity == "error" else CORE
    body: list[RenderableType] = [Text(message, style="title" if severity != "error" else "log.error")]
    if suggestions:
        table = Table.grid(padding=(0, 1), expand=False)
        table.add_column(no_wrap=True)
        table.add_column(overflow="fold")
        for suggestion in suggestions:
            table.add_row(Text(CORE, style="kryth.core"), Text(suggestion, style="muted"))
        body.extend([Text(""), table])
    return Panel(Group(*body), title=Text.assemble((glyph, style), (" " + title, f"bold {style}")), border_style=style, padding=(1, 2), expand=False, box=rich.box.ROUNDED)


def file_safety_header(path: str, *, added: int = 0, removed: int = 0, snapshot: bool = True) -> Text:
    del snapshot
    return Text.assemble(
        (CORE, "kryth.core"), (" patch ", "muted"), (path, "title"),
        (f"  +{added}", "log.success"), (f"  -{removed}", "log.error"),
    )


def model_route_line(model: str, phase: str) -> Text:
    return Text.assemble((CORE, "kryth.core"), (" model ", "muted"), (phase, "title"), ("  " + DOT + "  ", "muted"), (model, "title"))


def memory_line(summary: str) -> Text:
    return Text.assemble((CORE, "kryth.core"), (" memory ", "muted"), (summary, "title"))


__all__ = [
    "CognitionStep",
    "cognition_timeline",
    "diagnostic_card",
    "execution_graph",
    "file_safety_header",
    "hero_banner",
    "memory_line",
    "model_route_line",
    "startup_reveal",
]
