"""Supervisor Dashboard — rich terminal output for all supervisor subsystems.

Renders on demand (not on a live timer) to a Rich panel.
Uses existing UIStateManager + theme; never prints raw logs.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


def render_supervisor_dashboard() -> None:
    """Print the full supervisor status panel to the console."""
    parts = []

    # Health
    try:
        from agent.supervisor.health import health_engine
        health = health_engine.snapshot()
        parts.append(_health_section(health))
    except Exception:
        pass

    # Budget
    try:
        from agent.supervisor.budget import budget_controller
        snap = budget_controller.snapshot()
        parts.append(_budget_section(snap))
    except Exception:
        pass

    # Agent statuses
    try:
        from agent.supervisor.agent_supervisor import agent_supervisor
        statuses = agent_supervisor.all_statuses()
        if statuses:
            parts.append(_agents_section(statuses))
    except Exception:
        pass

    # Ownership locks
    try:
        from agent.supervisor.ownership import ownership_enforcer
        locks = ownership_enforcer.snapshot()
        if locks:
            parts.append(_locks_section(locks))
    except Exception:
        pass

    # Recovery events
    try:
        from agent.supervisor.recovery_manager import recovery_manager
        events = recovery_manager.all_events()
        if events:
            parts.append(_recovery_section(events))
    except Exception:
        pass

    if not parts:
        console.print("[muted](supervisor: no active mission)[/muted]")
        return

    body = Group(*parts)
    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "kryth.core"), ("  Execution Supervisor", "title")),
        title_align="left",
        border_style="mission.border",
        padding=(1, 2),
        expand=True,
    ))


def _health_section(health) -> Table:
    t = Table.grid(padding=(0, 2), expand=False)
    t.add_column(no_wrap=True, style="muted", min_width=18)
    t.add_column(overflow="fold")

    glyph = CORE if health.overall >= 70 else ERROR
    style = "mission.progress" if health.overall >= 70 else "mission.failed"
    t.add_row(
        Text.assemble((CORE, "kryth.core"), (" Mission Health", "muted")),
        Text.assemble((glyph, style), (f"  {health.overall}%  {health.label}", style)),
    )
    t.add_row(
        Text.assemble((CORE, "kryth.core"), (" Risk", "muted")),
        Text(health.risk_level.title(), style={
            "low": "approval.risk.low", "medium": "approval.risk.medium",
            "high": "approval.risk.high", "critical": "mission.failed",
        }.get(health.risk_level, "muted")),
    )
    for name, sub in sorted(health.subsystems.items()):
        glyph2 = CORE if sub.is_healthy else (WAITING if not sub.is_critical else ERROR)
        style2 = "eng.done" if sub.is_healthy else ("log.warn" if not sub.is_critical else "eng.failed")
        detail = f"  {DOT}  {sub.detail}" if sub.detail else ""
        t.add_row(
            Text(f"  {name.title()}", style="muted"),
            Text.assemble((glyph2, style2), (f"  {sub.score}%{detail}", style2)),
        )
    return t


def _budget_section(snap) -> Table:
    t = Table.grid(padding=(0, 2), expand=False)
    t.add_column(no_wrap=True, style="muted", min_width=18)
    t.add_column(overflow="fold")

    style = "mission.failed" if snap.any_exceeded else "term.metric.value"
    token_str = (
        f"{snap.tokens_spent:,} / {snap.token_limit:,}"
        if snap.token_limit else f"{snap.tokens_spent:,}"
    )
    t.add_row(
        Text.assemble((CORE, "kryth.core"), (" Tokens", "muted")),
        Text(token_str, style=style),
    )
    t.add_row(
        Text.assemble((CORE, "kryth.core"), (" Elapsed", "muted")),
        Text(f"{snap.elapsed_seconds:.0f}s", style="muted"),
    )
    t.add_row(
        Text.assemble((CORE, "kryth.core"), (" Agents", "muted")),
        Text(f"{snap.agent_spawns} spawned", style="muted"),
    )
    return t


def _agents_section(statuses) -> Table:
    t = Table.grid(padding=(0, 2), expand=False)
    t.add_column(no_wrap=True, min_width=16)
    t.add_column(no_wrap=True, min_width=10)
    t.add_column(overflow="fold", style="muted")

    for s in statuses:
        style = {
            "running": "agent.running", "idle": "log.warn",
            "done": "agent.done", "failed": "agent.failed",
            "crashed": "mission.failed", "deadlocked": "mission.failed",
        }.get(s.status, "muted")
        glyph = CORE if s.status == "done" else (ERROR if s.status in ("failed", "crashed") else WAITING)
        t.add_row(
            Text.assemble((glyph, style), (f"  {s.agent_id}", style)),
            Text(s.status, style=style),
            Text(s.role[:30], style="muted"),
        )
    return t


def _locks_section(locks: dict) -> Table:
    t = Table.grid(padding=(0, 2), expand=False)
    t.add_column(no_wrap=True, style="muted", min_width=18)
    t.add_column(overflow="fold")

    for key, info in list(locks.items())[:5]:
        short = key[key.find(":")+1:][:30]
        t.add_row(
            Text(short, style="muted"),
            Text(f"owned by {info['owner']}  {DOT}  {info['age_s']:.0f}s",
                 style="timeline.warn"),
        )
    return t


def _recovery_section(events) -> Table:
    t = Table.grid(padding=(0, 2), expand=False)
    t.add_column(no_wrap=True, min_width=18)
    t.add_column(overflow="fold")

    for ev in events[-5:]:
        glyph = CORE if ev.resolved else ERROR
        style = "eng.done" if ev.resolved else "eng.failed"
        t.add_row(
            Text.assemble((glyph, style), (f"  {ev.failure_class.value}", style)),
            Text(ev.resolution[:60], style="muted"),
        )
    return t
