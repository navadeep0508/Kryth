"""KRYTH Live Mission Control — NASA-style terminal dashboard.

Module-level registry so tool_scheduler can update the active dashboard
from any thread without passing the object through the call stack:

    from agent.ui.mission_control import get_active_mc
    mc = get_active_mc()
    if mc:
        mc.set_parallel(["read_file×3", "grep×1"])


Renders a live-updating panel when parallel agents are running:

  ╭─ ◈  Mission Control ──────────────────────────────────────────╮
  │  Build Authentication System                     [58%]       │
  │  ████████████░░░░░░░░░░░░░░░  Agents: 5  ·  Tasks: 12       │
  ├───────────────────────────────────────────────────────────────┤
  │  ◈ Lead Agent          Planning coordination                  │
  │  ◈ Frontend #1    ▶    editing Hero.jsx                      │
  │  ◈ Frontend #2    ▶    editing Navbar.jsx                    │
  │  ◈ Backend #1     ▶    writing auth.py                       │
  │  ✓ Backend #2          database schema done                   │
  ├───────────────────────────────────────────────────────────────┤
  │  Parallel: read_file×3  write_file×2  run_command×1          │
  ╰───────────────────────────────────────────────────────────────╯

Used by the scheduler during multi-agent runs. Call `start()` before
spawning agents and `stop()` after all layers complete.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, ERROR


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class _AgentRow:
    id: str
    role: str
    status: str = "waiting"   # waiting | running | done | failed | repairing
    task: str = ""
    layer: int = 0


@dataclass
class _MissionControlState:
    goal: str = ""
    progress: int = 0
    total_agents: int = 0
    active_agents: int = 0
    agents: Dict[str, _AgentRow] = field(default_factory=dict)
    parallel_tools: List[str] = field(default_factory=list)
    current_layer: int = 0
    total_layers: int = 0
    start_time: float = field(default_factory=time.monotonic)
    completed_agents: int = 0
    failed_agents: int = 0

    @property
    def elapsed(self) -> str:
        s = int(time.monotonic() - self.start_time)
        m, sec = divmod(s, 60)
        return f"{m:02d}:{sec:02d}"


# ---------------------------------------------------------------------------
# Status icons
# ---------------------------------------------------------------------------

_STATUS_ICON = {
    "waiting":   "[dim]⌛[/dim]",
    "running":   "[bold cyan]◈[/bold cyan]",
    "done":      "[bold green]✓[/bold green]",
    "failed":    "[bold red]✖[/bold red]",
    "repairing": "[bold yellow]⟳[/bold yellow]",
    "planning":  "[bold cyan]◈[/bold cyan]",
}

_STATUS_COLOR = {
    "waiting":   "dim",
    "running":   "cyan",
    "done":      "green",
    "failed":    "red",
    "repairing": "yellow",
    "planning":  "cyan",
}


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _render_control(state: _MissionControlState) -> Panel:
    """Build the Rich Panel for the current mission state."""

    # Header line: goal + elapsed
    header = Text()
    header.append(f"  {state.goal[:55]}", style="bold white")
    header.append(f"  [{state.elapsed}]", style="dim")

    # Progress bar (manual — no extra imports)
    pct = max(0, min(100, state.progress))
    filled = int(pct * 30 / 100)
    bar_filled = "█" * filled
    bar_empty = "░" * (30 - filled)
    progress_line = Text()
    progress_line.append(f"  {bar_filled}", style="bold cyan")
    progress_line.append(bar_empty, style="dim")
    progress_line.append(f"  {pct}%  ", style="bold")
    progress_line.append(
        f"Agents: {state.active_agents}/{state.total_agents}  ·  "
        f"Layer: {state.current_layer}/{state.total_layers}",
        style="dim",
    )

    # Agent table
    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(width=3)    # icon
    tbl.add_column(width=22)   # role
    tbl.add_column(width=4)    # arrow
    tbl.add_column()           # task

    for row in sorted(state.agents.values(), key=lambda r: (r.layer, r.id)):
        icon = _STATUS_ICON.get(row.status, "·")
        color = _STATUS_COLOR.get(row.status, "white")
        role_text = Text(row.role[:22], style=color)
        arrow = Text("▶  " if row.status == "running" else "   ")
        task_text = Text(row.task[:45], style="dim" if row.status != "running" else "white")
        tbl.add_row(Text.from_markup(icon), role_text, arrow, task_text)

    # Parallel tools line
    parallel_line = Text()
    if state.parallel_tools:
        parallel_line.append("  Parallel: ", style="dim")
        parallel_line.append("  ".join(state.parallel_tools[:6]), style="cyan")

    # Stats footer
    stats = Text()
    if state.completed_agents:
        stats.append(f"  ✓ {state.completed_agents} done  ", style="green")
    if state.failed_agents:
        stats.append(f"  ✖ {state.failed_agents} failed  ", style="red")

    # Compose
    lines = [header, progress_line, Text(""), tbl]
    if state.parallel_tools:
        lines.append(Text(""))
        lines.append(parallel_line)
    if stats.plain.strip():
        lines.append(stats)

    from rich.console import Group
    body = Group(*lines)

    return Panel(
        body,
        title=Text.from_markup(f"[bold]{CORE}  Mission Control[/bold]"),
        title_align="left",
        border_style="cyan",
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Live controller
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level active MC registry
# ---------------------------------------------------------------------------

_active_mc: "Optional[MissionControl]" = None
_active_mc_lock = threading.Lock()


def get_active_mc() -> "Optional[MissionControl]":
    with _active_mc_lock:
        return _active_mc


def _set_active_mc(mc: "Optional[MissionControl]") -> None:
    global _active_mc
    with _active_mc_lock:
        _active_mc = mc


class MissionControl:
    """Manages a Rich Live display during multi-agent execution.

    Usage:
        mc = MissionControl("Build Auth", total_agents=5, total_layers=2)
        mc.start()
        mc.set_agent("frontend_0", "Frontend #1", "running", "editing Hero.jsx", layer=1)
        mc.set_parallel(["read_file×3", "write_file×2"])
        mc.set_progress(40)
        mc.stop()
    """

    def __init__(self, goal: str, total_agents: int = 0, total_layers: int = 0) -> None:
        self._state = _MissionControlState(
            goal=goal,
            total_agents=total_agents,
            total_layers=total_layers,
        )
        self._lock = threading.RLock()
        self._live: Optional[Live] = None
        self._refresh_thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # State mutation (thread-safe)

    def set_agent(self, agent_id: str, role: str, status: str, task: str = "", layer: int = 0) -> None:
        prev_status = None
        with self._lock:
            if agent_id not in self._state.agents:
                self._state.agents[agent_id] = _AgentRow(id=agent_id, role=role, layer=layer)
            row = self._state.agents[agent_id]
            prev_status = row.status
            row.role = role
            row.status = status
            row.task = task[:60]
            row.layer = layer
            self._state.active_agents = sum(
                1 for r in self._state.agents.values() if r.status == "running"
            )
            self._state.completed_agents = sum(
                1 for r in self._state.agents.values() if r.status == "done"
            )
            self._state.failed_agents = sum(
                1 for r in self._state.agents.values() if r.status == "failed"
            )

        # When Live is unavailable, print status transitions as plain text
        if self._live is None and prev_status != status:
            icon = {"running": "◈", "done": "✓", "failed": "✗", "repairing": "⟳", "waiting": "⌛"}.get(status, "·")
            try:
                console.print(f"  {icon} {role}  {task[:50]}" if task else f"  {icon} {role}")
            except Exception:
                pass

        self._refresh()

    def set_progress(self, percent: int) -> None:
        with self._lock:
            self._state.progress = percent
        self._refresh()

    def set_layer(self, current: int) -> None:
        with self._lock:
            self._state.current_layer = current
        self._refresh()

    def set_parallel(self, tool_labels: List[str]) -> None:
        with self._lock:
            self._state.parallel_tools = tool_labels
        self._refresh()

    # ------------------------------------------------------------------
    # Lifecycle

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        _set_active_mc(self)

        # Stop the activity spinner BEFORE starting Live — Rich only allows
        # one Live display at a time. The spinner is itself a Live; starting
        # another one while it runs silently fails or corrupts the display.
        try:
            from agent.ui.renderer import _activity
            _activity.idle()
        except Exception:
            pass

        try:
            # Ensure no other Live display is active (Rich allows only one)
            self._live = Live(
                _render_control(self._state),
                console=console,
                refresh_per_second=4,
                transient=True,   # clears on stop — no permanent residue at bottom
                auto_refresh=True,
            )
            self._live.__enter__()
        except Exception:
            self._live = None
            # Print a plain-text header as fallback so the user sees the dashboard
            try:
                console.print(f"\n  ◈ Mission Control — {self._state.goal[:60]}")
                console.print(f"  ◈ {self._state.total_agents} agents  ·  {self._state.total_layers} layers\n")
            except Exception:
                pass

    def stop(self) -> None:
        self._running = False
        _set_active_mc(None)
        if self._live:
            try:
                self._live.__exit__(None, None, None)
            except Exception:
                pass
            self._live = None
        # Print a one-line completion summary after the transient panel clears
        try:
            done = self._state.completed_agents
            fail = self._state.failed_agents
            status = "✓" if not fail else "▲"
            style = "bold green" if not fail else "bold yellow"
            console.print(
                f"  [{style}]{status} Mission Control — {done}/{self._state.total_agents} agents done"
                + (f"  ({fail} failed)" if fail else ""),
                markup=True,
            )
        except Exception:
            pass

    def _refresh(self) -> None:
        if self._live and self._running:
            try:
                with self._lock:
                    panel = _render_control(self._state)
                self._live.update(panel)
            except Exception:
                pass

    def __enter__(self) -> "MissionControl":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


# ---------------------------------------------------------------------------
# Mission Summary renderer (end-of-mission)
# ---------------------------------------------------------------------------

def render_mission_summary(
    goal: str,
    duration_s: float,
    agents_used: int,
    peak_parallel: int,
    files_read: int,
    files_modified: int,
    commands: int,
    tests_run: int,
    cache_hits: int,
    repairs: int,
    context_saves: int,
    parallel_speedup: float = 1.0,
    test_cache_saved: float = 0.0,
) -> None:
    """Print the end-of-mission summary panel."""
    m, s = divmod(int(duration_s), 60)
    dur_str = f"{m}m {s:02d}s"

    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(style="dim", width=24)
    tbl.add_column(style="bold white")

    def _row(label: str, value: str, style: str = "white") -> None:
        tbl.add_row(label, Text(value, style=style))

    _row("Duration", dur_str)
    _row("Agents Used", str(agents_used))
    _row("Peak Parallelism", str(peak_parallel))
    _row("Files Read", str(files_read))
    _row("Files Modified", str(files_modified))
    _row("Commands", str(commands))
    _row("Tests Run", str(tests_run))
    _row("Cache Hits", str(cache_hits))
    _row("Repairs", str(repairs))
    _row("Context Saves", str(context_saves))

    perf_tbl = Table.grid(padding=(0, 2))
    perf_tbl.add_column(style="dim", width=24)
    perf_tbl.add_column()

    if parallel_speedup > 1.0:
        perf_tbl.add_row("Parallel Execution", Text(f"{parallel_speedup:.1f}x faster", style="bold cyan"))
    if test_cache_saved > 0:
        perf_tbl.add_row("Test Cache", Text(f"{test_cache_saved:.0%} saved", style="bold green"))

    from rich.console import Group
    from rich.rule import Rule

    body = Group(tbl, Rule(style="dim"), perf_tbl, Text(""), Text("✓ Mission Complete", style="bold green"))

    console.print(Panel(
        body,
        title=Text.from_markup(f"[bold]{CORE}  Mission Summary[/bold]"),
        title_align="left",
        border_style="green",
        padding=(1, 2),
    ))
