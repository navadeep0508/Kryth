"""Orchestration team dashboard — isolated panels, no shared log stream.

During DAG/SWARM execution the screen must read like an engineering-org
dashboard: one isolated panel per TEAM, plus an executive panel, a dependency
panel, and a small tool-activity panel — NOT a shared stream of interleaved
worker logs.

This module owns:
  • ``TeamDashboardState`` — directly-testable aggregation of scheduler events
    into per-team status (no global state needed).
  • ``render_dashboard(state)`` — the isolated-panels renderable.
  • ``OrchestrationDashboard`` — a Rich ``Live`` controller that subscribes to
    the bus and refreshes the panels in place. TTY-gated and flag-gated so tests
    and headless runs never start a live terminal; presentation-only.

Agents map to teams by role: the team contract sets each agent's role to the
team label (Frontend Team, Backend Team, …), so ``AGENT_*`` events with that
name aggregate into the matching team panel.
"""

from __future__ import annotations

import os
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.events import EventKind


def dashboard_enabled() -> bool:
    """Default ON; KRYTH_TEAM_DASHBOARD=0 disables (falls back to clean view)."""
    return os.environ.get("KRYTH_TEAM_DASHBOARD", "1").strip().lower() not in ("0", "false", "no", "off")


@dataclass
class TeamPanelState:
    name: str
    status: str = "WAITING"          # WAITING | ACTIVE | BLOCKED | COMPLETE | FAILED
    progress: int = 0
    current: str = ""
    owner: str = ""


@dataclass
class TeamDashboardState:
    teams: Dict[str, TeamPanelState] = field(default_factory=dict)
    deps: List[dict] = field(default_factory=list)            # [{label,status}] / DAG nodes
    recent: Deque[tuple] = field(default_factory=lambda: deque(maxlen=10))  # (team, action)
    risk: str = "low"
    eta_min: float = 0.0
    bottlenecks: int = 0
    work_steals: int = 0
    _last_team: str = ""
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # -- aggregation --------------------------------------------------------

    def _team(self, name: str) -> TeamPanelState:
        t = self.teams.get(name)
        if t is None:
            t = TeamPanelState(name=name, owner=f"{name} Lead")
            self.teams[name] = t
        return t

    def apply(self, kind: EventKind, data: dict) -> None:
        """Fold one UI event into the dashboard state. Never raises."""
        try:
            with self._lock:
                self._apply(kind, data or {})
        except Exception:
            pass

    def _apply(self, kind: EventKind, d: dict) -> None:
        name = str(d.get("name") or d.get("agent_id") or "")
        if kind == EventKind.AGENT_CREATED and name:
            self._team(name).status = "WAITING"
            self._last_team = name
        elif kind == EventKind.AGENT_TASK_START and name:
            t = self._team(name); t.status = "ACTIVE"
            t.current = str(d.get("description") or d.get("task") or t.current)
            self._last_team = name
        elif kind == EventKind.AGENT_UPDATE and name:
            t = self._team(name)
            if d.get("status"):
                t.status = _norm_status(d["status"])
            if d.get("progress") is not None:
                t.progress = max(0, min(100, int(d["progress"] or 0)))
            if d.get("task") or d.get("step"):
                t.current = str(d.get("task") or d.get("step"))
            self._last_team = name
        elif kind == EventKind.AGENT_TASK_DONE and name:
            t = self._team(name); t.status = "COMPLETE"; t.progress = 100
        elif kind == EventKind.AGENT_FAILED and name:
            self._team(name).status = "FAILED"
        elif kind == EventKind.WORK_STOLEN:
            self.work_steals += 1
        elif kind == EventKind.DAG_UPDATE:
            nodes = d.get("nodes")
            if isinstance(nodes, list):
                self.deps = nodes
        elif kind == EventKind.QUEUE_STATUS:
            # crude bottleneck signal: blocked > 0
            self.bottlenecks = int(d.get("blocked", 0) or 0)
        elif kind in (EventKind.TOOL_START, EventKind.TOOL_RESULT):
            # Attribute the tool action to the last-active team (best effort).
            action = str(d.get("name") or d.get("action") or "")
            target = ""
            args = d.get("args") or {}
            if isinstance(args, dict):
                target = str(args.get("path") or args.get("file") or args.get("target") or "")
            if action:
                label = (f"{action} {target}".strip()) if target else action
                self.recent.append((self._last_team or "team", label))

    # -- summary ------------------------------------------------------------

    def overall_progress(self) -> int:
        with self._lock:
            if not self.teams:
                return 0
            return round(sum(t.progress for t in self.teams.values()) / len(self.teams))

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for t in self.teams.values() if t.status == "ACTIVE")

    def utilization(self) -> int:
        with self._lock:
            n = len(self.teams)
            return round(100 * self.active_count() / n) if n else 0


def _norm_status(s: str) -> str:
    s = str(s).lower()
    return {"running": "ACTIVE", "active": "ACTIVE", "complete": "COMPLETE",
            "done": "COMPLETE", "failed": "FAILED", "error": "FAILED",
            "blocked": "BLOCKED", "waiting": "WAITING", "idle": "WAITING"}.get(s, s.upper())


_STATUS_STYLE = {
    "ACTIVE": "v3.step.active", "COMPLETE": "v3.step.done", "FAILED": "term.failed",
    "BLOCKED": "log.warn", "WAITING": "v3.step.pending",
}


def _bar(pct: int, width: int = 12) -> str:
    pct = max(0, min(100, pct))
    fill = round(width * pct / 100)
    return "█" * fill + "░" * (width - fill)


# ── Panel rendering (isolated panels — never a shared stream) ─────────────────

def _team_panel(t: TeamPanelState) -> Panel:
    style = _STATUS_STYLE.get(t.status, "v3.meta.val")
    body = Table.grid(padding=(0, 2))
    body.add_column(style="v3.meta.key", no_wrap=True, min_width=9)
    body.add_column(style="v3.meta.val", no_wrap=True)
    body.add_row("Progress", Text(f"{_bar(t.progress)} {t.progress}%", style=style))
    body.add_row("Status", Text(t.status, style=style))
    body.add_row("Current", t.current or "—")
    body.add_row("Owner", t.owner or f"{t.name} Lead")
    return Panel(body, title=Text(t.name.upper(), style="v3.card.title"),
                 title_align="left", border_style=style, box=rich.box.ROUNDED,
                 padding=(0, 1), expand=True)


def _executive_panel(state: TeamDashboardState) -> Panel:
    g = Table.grid(padding=(0, 3))
    g.add_column(style="v3.meta.key", no_wrap=True, min_width=12)
    g.add_column(style="v3.meta.val", no_wrap=True)
    g.add_row(Text("Risk", style="v3.meta.key"),
              Text(str(state.risk).upper(),
                   style={"low": "v3.step.done", "medium": "log.warn",
                          "high": "term.failed"}.get(str(state.risk).lower(), "v3.meta.val")))
    g.add_row("ETA", f"{state.eta_min:.0f}m" if state.eta_min else "—")
    g.add_row("Bottlenecks", str(state.bottlenecks))
    g.add_row("Utilization", f"{state.utilization()}%")
    g.add_row("Progress", f"{state.overall_progress()}%")
    return Panel(g, title=Text("EXECUTIVE", style="kryth.core"), title_align="left",
                 border_style="mission.border", box=rich.box.DOUBLE_EDGE,
                 padding=(0, 2), expand=False)


def _dependency_panel(state: TeamDashboardState) -> Optional[Panel]:
    if not state.deps:
        return None
    body = Text()
    glyph = {"running": "◐", "active": "◐", "complete": "●", "done": "●",
             "blocked": "◌", "failed": "✗", "waiting": "○", "pending": "○"}
    for n in state.deps[:12]:
        st = str(n.get("status", "waiting")).lower()
        body.append(f"  {glyph.get(st, '○')} ", style=_STATUS_STYLE.get(_norm_status(st), "v3.meta.val"))
        body.append(str(n.get("label") or n.get("id") or "?"), style="v3.meta.val")
        body.append(f"   {st.upper()}\n", style="v3.duration")
    return Panel(body, title=Text("DEPENDENCIES", style="kryth.core"), title_align="left",
                 border_style="v3.card.border", box=rich.box.ROUNDED, padding=(0, 2), expand=False)


def _tool_activity_panel(state: TeamDashboardState) -> Optional[Panel]:
    rows = list(state.recent)[-10:]
    if not rows:
        return None
    body = Text()
    for team, action in rows:
        body.append(f"  {team}: ", style="agent.name")
        body.append(f"{action}\n", style="v3.meta.val")
    return Panel(body, title=Text("RECENT ACTIONS", style="kryth.core"), title_align="left",
                 border_style="v3.card.border", box=rich.box.ROUNDED, padding=(0, 2), expand=False)


def render_dashboard(state: TeamDashboardState):
    """Build the isolated-panels renderable: executive + team grid + dependency
    + tool activity. No shared log stream."""
    with state._lock:
        teams = list(state.teams.values())
    sections = [_executive_panel(state)]

    if teams:
        grid = Table.grid(padding=(1, 1), expand=True)
        cols = 1 if len(teams) <= 2 else 2 if len(teams) <= 8 else 3
        for _ in range(cols):
            grid.add_column(ratio=1)
        panels = [_team_panel(t) for t in teams]
        for i in range(0, len(panels), cols):
            grid.add_row(*panels[i:i + cols])
        sections.append(Panel(grid, title=Text(f"TEAMS ({len(teams)})", style="kryth.core"),
                              title_align="left", border_style="v3.card.border",
                              box=rich.box.ROUNDED, padding=(0, 1), expand=True))

    dep = _dependency_panel(state)
    if dep is not None:
        sections.append(dep)
    act = _tool_activity_panel(state)
    if act is not None:
        sections.append(act)
    return Group(*sections)


# ── Live controller ──────────────────────────────────────────────────────────

class OrchestrationDashboard:
    """Subscribes to the bus and Live-refreshes the team panels during a mission.

    Start/stop around the scheduler. TTY+flag gated: if stdout is not a TTY or
    the flag is off, it still aggregates state (for tests) but does not start a
    Live terminal (so headless runs and the test suite are unaffected)."""

    def __init__(self, *, eta_min: float = 0.0, risk: str = "low"):
        self.state = TeamDashboardState(eta_min=eta_min, risk=risk)
        self._unsub = None
        self._live = None

    def _on_event(self, evt) -> None:
        self.state.apply(evt.kind, evt.data)
        if self._live is not None:
            try:
                self._live.update(render_dashboard(self.state))
            except Exception:
                pass

    def start(self) -> "OrchestrationDashboard":
        from agent.ui.events import BUS
        self._unsub = BUS.subscribe(self._on_event)
        # Only take over the terminal when interactive + enabled.
        try:
            import sys
            interactive = bool(sys.stdout) and sys.stdout.isatty()
        except Exception:
            interactive = False
        if interactive and dashboard_enabled():
            try:
                from rich.live import Live
                from agent.ui.console import console
                self._live = Live(render_dashboard(self.state), console=console,
                                  refresh_per_second=4, transient=False)
                self._live.start()
            except Exception:
                self._live = None
        return self

    def stop(self) -> None:
        if self._unsub:
            try:
                self._unsub()
            except Exception:
                pass
            self._unsub = None
        if self._live is not None:
            try:
                self._live.update(render_dashboard(self.state))
                self._live.stop()
            except Exception:
                pass
            self._live = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False
