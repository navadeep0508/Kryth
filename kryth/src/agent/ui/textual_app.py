"""Textual multi-pane dashboard (Phase 12).

A full TUI for parallel missions: left = live DAG, center = agent grid,
right = metrics (efficiency / critical path), bottom = live tool activity. It
consumes the same ``EngineState`` + event ``BUS`` the Rich Live engine uses, so
no execution/scheduler coupling is introduced — it is a second *view* over the
existing event stream.

Activation is opt-in and degrades safely:

* ``KRYTH_LIVE_UI=textual`` (or ``KRYTH_TUI=1``) selects this view.
* If the ``textual`` package is missing, ``launch()`` returns ``False`` and the
  caller keeps the Rich Live engine — never a crash.

The pure renderable-builders (``build_dag``, ``build_agent_grid``,
``build_metrics``, ``build_activity``) are importable and testable WITHOUT
starting an event loop; the ``App`` subclass only wires them to widgets.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import rich.box

from agent.ui.events import BUS, Event, EventKind


def textual_available() -> bool:
    try:
        import textual  # noqa: F401
        return True
    except Exception:
        return False


def tui_selected() -> bool:
    """True when the user explicitly asked for the Textual view."""
    if os.environ.get("KRYTH_TUI", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return os.environ.get("KRYTH_LIVE_UI", "").strip().lower() == "textual"


# ── Pure renderable builders (testable without a running app) ────────────────

_DAG_GLYPH = {
    "waiting": ("○", "yellow"), "pending": ("○", "yellow"),
    "running": ("◐", "bright_cyan"), "active": ("◐", "bright_cyan"),
    "complete": ("●", "green"), "done": ("●", "green"),
    "failed": ("✗", "red"), "blocked": ("◌", "yellow"),
}


def build_dag(nodes: list) -> Panel:
    """Left pane — the live DAG tree."""
    body = Text()
    nodes = list(nodes or [])
    if not nodes:
        body.append("  (no DAG yet)", style="dim")
    else:
        roots = [n for n in nodes if not n.get("deps")] or nodes[:1]
        seen = set()

        def line(n, ind):
            st = str(n.get("status", "waiting")).lower()
            g, sty = _DAG_GLYPH.get(st, ("○", "yellow"))
            body.append("  " * ind)
            body.append(f"{g} ", style=sty)
            body.append(str(n.get("label") or n.get("id") or "?"))
            extra = "  ".join(str(n[k]) for k in ("owner", "duration") if n.get(k))
            if extra:
                body.append(f"   {extra}", style="dim")
            body.append("\n")

        for r in roots:
            line(r, 0); seen.add(r.get("id"))
            for n in nodes:
                if n.get("id") not in seen and r.get("id") in (n.get("deps") or []):
                    line(n, 1); seen.add(n.get("id"))
        for n in nodes:
            if n.get("id") not in seen:
                line(n, 1)
    return Panel(body, title="Execution DAG", border_style="cyan", box=rich.box.ROUNDED)


def build_agent_grid(agents: dict) -> Panel:
    """Center pane — persistent agent cards. Scales to many agents via a grid."""
    agents = dict(agents or {})
    if not agents:
        return Panel(Text("  (no agents)", style="dim"), title="Agents",
                     border_style="green", box=rich.box.ROUNDED)
    grid = Table.grid(padding=(0, 1), expand=True)
    cols = 1 if len(agents) <= 4 else 2 if len(agents) <= 12 else 3
    for _ in range(cols):
        grid.add_column(ratio=1)
    cards = []
    for a in agents.values():
        st = str(a.get("status", "")).lower()
        sty = ("green" if st in ("complete", "done")
               else "red" if st in ("failed", "error") else "bright_cyan")
        c = Text()
        c.append(str(a.get("name", "agent")) + "\n", style=f"bold {sty}")
        c.append(f"{st or 'idle'}\n", style=sty)
        if a.get("step") or a.get("file") or a.get("tool"):
            c.append(str(a.get("step") or a.get("file") or a.get("tool")) + "\n", style="white")
        prog = max(0, min(100, int(a.get("progress", 0) or 0)))
        fill = round(10 * prog / 100)
        c.append("█" * fill + "░" * (10 - fill), style=sty)
        c.append(f" {prog}%", style="dim")
        cards.append(Panel(c, border_style=sty, box=rich.box.ROUNDED, expand=True))
    for i in range(0, len(cards), cols):
        grid.add_row(*cards[i:i + cols])
    return Panel(grid, title=f"Agents ({len(agents)})", border_style="green", box=rich.box.ROUNDED)


def build_metrics(state) -> Panel:
    """Right pane — parallel efficiency + critical path."""
    agents = dict(getattr(state, "agents", {}) or {})
    total = len(agents)
    run = sum(1 for c in agents.values()
              if str(c.get("status", "")).lower() in ("running", "active"))
    eff = round(100 * run / total) if total else 0
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold"); t.add_column()
    t.add_row("Workers", str(total))
    t.add_row("Running", str(run))
    t.add_row("Idle", str(total - run))
    t.add_row("Peak", str(getattr(state, "peak_parallel", 0)))
    t.add_row("Steals", str(getattr(state, "work_steals", 0)))
    t.add_row("Efficiency", Text(f"{eff}%", style="green" if eff >= 60 else "yellow"))

    pieces: list[Any] = [t]
    try:
        from agent.ui.dag_analysis import critical_path
        cp = critical_path(getattr(state, "dag_nodes", []))
        if cp.path:
            ct = Text("\nCritical path\n", style="bold")
            for i, lbl in enumerate(cp.labels):
                neck = cp.path[i] == cp.bottleneck_id
                ct.append(f"  {lbl}" + ("  ◀" if neck else "") + "\n",
                          style="yellow" if neck else "white")
            ct.append(f"  impact {cp.impact} · blocks {cp.blocked_workers}", style="dim")
            pieces.append(ct)
    except Exception:
        pass
    return Panel(Group(*pieces), title="Metrics", border_style="magenta", box=rich.box.ROUNDED)


def build_provider_health(rows: list) -> Panel:
    """Provider health panel — only shown in DAG/SWARM, never in DIRECT.

    Phase 6: integrates provider health into the Textual dashboard.
    rows: list of dicts with keys: provider, status, timeouts, retries, success_rate
    """
    rows = list(rows or [])
    body = Table.grid(padding=(0, 2), expand=False)
    for _ in range(5):
        body.add_column(no_wrap=True)
    if not rows:
        empty = Text("  (no provider data)", style="dim")
        return Panel(empty, title="Provider Health", border_style="dim cyan", box=rich.box.ROUNDED)

    _style = {"healthy": "green", "degraded": "yellow", "unhealthy": "red"}
    for row in rows:
        st = str(row.get("status", "healthy")).lower()
        sty = _style.get(st, "white")
        body.add_row(
            Text(str(row.get("provider", ""))[:22], style="white"),
            Text(st.upper(), style=f"bold {sty}"),
            Text(str(row.get("timeouts", 0)), style="dim"),
            Text(str(row.get("retries", 0)), style="dim"),
            Text(f"{row.get('success_rate', 100):.0f}%", style=sty),
        )
    return Panel(body, title="◈  Provider Health", border_style="cyan", box=rich.box.ROUNDED)


def build_activity(tools) -> Panel:
    """Bottom pane — recent tool activity, most recent last."""
    rows = list(tools or [])[-12:]
    body = Text()
    if not rows:
        body.append("  (idle)", style="dim")
    for item in rows:
        action, target, st = (item if isinstance(item, (list, tuple)) and len(item) >= 3
                              else (str(item), "", "done"))
        glyph = "✓" if st == "done" else "✗" if st in ("error", "failed") else "▸"
        body.append(f"  {glyph} ", style="green" if st == "done" else "white")
        body.append(str(action), style="bold")
        if target:
            body.append(f"  {target}", style="dim")
        body.append("\n")
    return Panel(body, title="Tool Activity", border_style="blue", box=rich.box.ROUNDED)


# ── Textual App (only defined if textual is importable) ──────────────────────

def make_app(state):
    """Build a KrythDashboard App bound to `state`. Returns None if textual is
    unavailable. Kept as a factory so importing this module never hard-requires
    textual."""
    if not textual_available():
        return None

    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Static

    class KrythDashboard(App):
        CSS = """
        Screen { layout: vertical; }
        #top { height: 1fr; }
        #dag { width: 1fr; }
        #grid { width: 2fr; }
        #metrics { width: 1fr; }
        #activity { height: 10; }
        """
        TITLE = "KRYTH — Mission Dashboard"

        def __init__(self, st):
            super().__init__()
            self._st = st
            self._unsub = None

        def compose(self) -> "ComposeResult":
            ph_rows = getattr(self._st, "provider_health_rows", [])
            with Vertical():
                with Horizontal(id="top"):
                    yield Static(build_dag(getattr(self._st, "dag_nodes", [])), id="dag")
                    yield Static(build_agent_grid(getattr(self._st, "agents", {})), id="grid")
                    yield Static(build_metrics(self._st), id="metrics")
                yield Static(build_activity(getattr(self._st, "tools", [])), id="activity")
                if ph_rows:
                    yield Static(build_provider_health(ph_rows), id="provider_health")

        def _redraw(self) -> None:
            try:
                ph_rows = getattr(self._st, "provider_health_rows", [])
                self.query_one("#dag", Static).update(build_dag(getattr(self._st, "dag_nodes", [])))
                self.query_one("#grid", Static).update(build_agent_grid(getattr(self._st, "agents", {})))
                self.query_one("#metrics", Static).update(build_metrics(self._st))
                self.query_one("#activity", Static).update(build_activity(getattr(self._st, "tools", [])))
                try:
                    self.query_one("#provider_health", Static).update(build_provider_health(ph_rows))
                except Exception:
                    pass
            except Exception:
                pass

        def on_mount(self) -> None:
            # Re-render on a light timer; the engine mutates `state` from bus events.
            self.set_interval(0.25, self._redraw)

    return KrythDashboard(state)


def launch(state) -> bool:
    """Run the Textual dashboard if selected AND available. Returns True if it
    ran (and has now exited), False if the caller should use another view."""
    if not tui_selected() or not textual_available():
        return False
    app = make_app(state)
    if app is None:
        return False
    try:
        app.run()
        return True
    except Exception:
        return False  # any TUI failure → caller falls back to Rich Live
