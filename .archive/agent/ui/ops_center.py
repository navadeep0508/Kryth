"""KRYTH Operations Center V2 (UI / observability only).

A command-center view layer for DAG/SWARM/multi-agent/portfolio missions. It
renders the organization the org-layer models describe — portfolio, departments,
dependencies, teams, decisions, economics — so an operator understands system
health in five seconds instead of reading log spam.

STRICT SCOPE (the headline requirement):

* It **activates only** for DAG mode, SWARM mode, multi-agent / multi-team
  missions, and portfolio execution.
* It **never activates** for conversation, fast path, direct mode, single-agent
  tasks, simple coding, or file operations.

It changes NOTHING about execution, routing, orchestration, the scheduler, the
DAG engine, or agent behavior. It only reads snapshots and draws panels.

Progressive disclosure scales UI complexity with worker count:
    1 worker      → minimal (defer to the normal execution UI; no ops center)
    2–5 workers   → team view
    5–15 workers  → operations center
    15+ workers   → full organizational view
"""

from __future__ import annotations

import os
from typing import Optional

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.panels import _print_panel
from agent.ui.theme import CORE


# ── Activation gate (the critical scope limit) ───────────────────────────────

# Execution modes that may show the ops center.
_PARALLEL_MODES = {"dag", "swarm", "portfolio"}
# Modes that must NEVER show it.
_SIMPLE_MODES = {"direct", "fast", "fast_path", "conversation", "conversational", "single"}


def should_activate(*, mode: str = "", workers: int = 1,
                    is_conversational: bool = False, multi_agent: bool = False,
                    portfolio: bool = False) -> bool:
    """True only for genuine multi-agent orchestration.

    A task is ops-center-eligible iff it is conversational=False AND
    (mode in {dag, swarm, portfolio} OR it is explicitly multi-agent/portfolio
    with more than one worker). Any simple/direct/fast/conversation signal hard
    disqualifies — matching the spec's MUST-NOT list."""
    if is_conversational:
        return False
    m = (mode or "").strip().lower()
    if m in _SIMPLE_MODES:
        return False
    if m in _PARALLEL_MODES:
        return True
    if portfolio:
        return True
    if multi_agent and workers >= 2:
        return True
    return False


def disclosure_level(workers: int) -> str:
    """Progressive disclosure tier from worker count."""
    if workers <= 1:
        return "minimal"
    if workers <= 5:
        return "team"
    if workers <= 15:
        return "ops_center"
    return "org"


def env_enabled() -> bool:
    """Master switch — default OFF so nothing changes unless opted in.
    The activation gate still applies on top of this."""
    return os.environ.get("KRYTH_OPS_CENTER", "").strip().lower() in ("1", "true", "yes", "on")


def active(*, mode: str = "", workers: int = 1, is_conversational: bool = False,
           multi_agent: bool = False, portfolio: bool = False) -> bool:
    """The real gate used by callers: env switch AND eligibility AND a tier that
    actually shows the center (team/ops_center/org, never minimal)."""
    if not env_enabled():
        return False
    if not should_activate(mode=mode, workers=workers, is_conversational=is_conversational,
                           multi_agent=multi_agent, portfolio=portfolio):
        return False
    return disclosure_level(workers) != "minimal"


# ── Phase 1 — Operations Center header ───────────────────────────────────────

def _risk_style(level: str) -> str:
    return {"low": "v3.step.done", "medium": "log.warn", "high": "term.failed"}.get(
        str(level).lower(), "v3.meta.val")


def operations_header(*, active_missions: int = 0, running_workers: int = 0,
                      utilization: float = 0.0, bottlenecks: int = 0,
                      risk: str = "low", budget_usage: float = 0.0,
                      success_rate: float = 0.0, eta_min: float = 0.0) -> None:
    """Phase 1 — the at-a-glance health panel."""
    g = Table.grid(padding=(0, 4), expand=False)
    g.add_column(style="v3.statusbar", no_wrap=True, min_width=18)
    g.add_column(style="v3.statusbar.accent", no_wrap=True)
    g.add_row("Active missions", str(active_missions))
    g.add_row("Running workers", str(running_workers))
    g.add_row("Utilization", f"{utilization*100:.0f}%" if utilization <= 1 else f"{utilization:.0f}%")
    g.add_row("Bottlenecks", str(bottlenecks))
    g.add_row(Text("Risk level", style="v3.statusbar"),
              Text(str(risk).upper(), style=_risk_style(risk)))
    g.add_row("Budget usage", f"{budget_usage*100:.0f}%" if budget_usage <= 1 else f"{budget_usage:.0f}%")
    g.add_row("Success rate", f"{success_rate*100:.0f}%" if success_rate <= 1 else f"{success_rate:.0f}%")
    g.add_row("ETA", f"{eta_min:.0f}m" if eta_min else "—")
    console.print()
    _print_panel(Panel(
        g, title=Text.assemble((CORE, "kryth.core"), ("  KRYTH OPERATIONS CENTER", "kryth.core")),
        title_align="left", border_style="mission.border", padding=(1, 2),
        expand=False, box=rich.box.DOUBLE_EDGE))


def _bar(pct: float, width: int = 10) -> str:
    pct = max(0.0, min(100.0, pct))
    fill = round(width * pct / 100)
    return "█" * fill + "░" * (width - fill)


# ── Phase 2 — Portfolio view ─────────────────────────────────────────────────

def portfolio_view(missions: list) -> None:
    if not missions:
        return
    body = Table.grid(padding=(0, 2), expand=False)
    for _ in range(5):
        body.add_column(no_wrap=True)
    for m in missions[:20]:
        st = str(m.get("state", "queued"))
        dot = {"running": "v3.step.active", "completed": "v3.step.done",
               "blocked": "term.failed", "paused": "log.warn"}.get(st, "v3.step.pending")
        body.add_row(
            Text("● ", style=dot) + Text(str(m.get("title") or m.get("id")), style="v3.meta.val"),
            Text(_bar(m.get("progress", 0)), style="v3.step.active"),
            Text(f"{m.get('progress', 0):.0f}%", style="v3.duration"),
            Text(str(m.get("risk", "low")).upper(), style=_risk_style(m.get("risk", "low"))),
            Text(f"{m.get('eta_min', 0):.0f}m" if m.get("eta_min") else "—", style="v3.duration"),
        )
    _panel(body, "Mission Portfolio")


# ── Phase 3 — Organization chart ─────────────────────────────────────────────

def organization_view(chart_rows: list) -> None:
    if not chart_rows:
        return
    body = Text()
    role_style = {"head": "v3.card.title", "lead": "v3.meta.accent", "worker": "v3.meta.val"}
    for r in chart_rows:
        body.append("  " * r.get("depth", 0))
        body.append("• ", style="v3.duration")
        body.append(str(r.get("name") or r.get("id")), style=role_style.get(r.get("role"), "v3.meta.val"))
        if r.get("role") != "worker":
            body.append(f"  [{r.get('role')}]", style="v3.duration")
        if r.get("busy"):
            body.append("  ● running", style="v3.step.active")
        body.append("\n")
    _panel(body, "Organization")


# ── Phase 4 — Dependency radar ───────────────────────────────────────────────

def dependency_radar(blocking: dict, consumers: Optional[list] = None) -> None:
    if not blocking or not blocking.get("dependency"):
        return
    body = Text()
    body.append(f"  {blocking['dependency']}\n", style="v3.meta.accent")
    body.append(f"  Blocking   {blocking.get('blocking', 0)} tasks\n", style="v3.meta.val")
    body.append(f"  Waiting    {blocking.get('workers_waiting', 0)} workers\n", style="v3.meta.val")
    body.append(f"  Impact     ", style="v3.meta.val")
    body.append(f"{str(blocking.get('impact', 'low')).upper()}\n", style=_risk_style(blocking.get("impact", "low")))
    if consumers:
        body.append("  Consumers:\n", style="v3.duration")
        for c in consumers[:10]:
            body.append(f"    • {c}\n", style="v3.meta.val")
    _panel(body, "Dependency Radar")


# ── Phase 5 — Team utilization ───────────────────────────────────────────────

def team_view(departments: list) -> None:
    if not departments:
        return
    body = Table.grid(padding=(0, 2), expand=False)
    for _ in range(4):
        body.add_column(no_wrap=True)
    for d in departments[:15]:
        util = d.get("utilization", 0)
        pct = util * 100 if util <= 1 else util
        body.add_row(
            Text(str(d.get("name")), style="agent.name"),
            Text(_bar(pct), style="v3.step.active" if pct < 100 else "v3.step.done"),
            Text(f"{pct:.0f}%", style="v3.duration"),
            Text(f"cap {d.get('capacity', 0)}  rep {d.get('reputation', 0):.0%}", style="v3.duration"),
        )
    _panel(body, "Team Utilization")


# ── Phase 6 — Executive decision feed ────────────────────────────────────────

def decision_feed(decisions: list) -> None:
    if not decisions:
        return
    body = Text()
    for d in decisions[-12:]:
        ts = d.get("at") or ""
        body.append(f"  {ts}  " if ts else "  ", style="v3.duration")
        body.append(str(d.get("text", "")), style="v3.meta.val")
        if d.get("data", {}).get("reason"):
            body.append(f"\n      reason: {d['data']['reason']}", style="v3.duration")
        body.append("\n")
    _panel(body, "Executive Decisions")


# ── Phase 7 — Live timeline ──────────────────────────────────────────────────

def timeline_view(events: list) -> None:
    if not events:
        return
    body = Text()
    for e in events[-20:]:
        ts = e.get("at") or e.get("ts") or ""
        body.append(f"  {ts}  " if ts else "  ", style="v3.duration")
        body.append(str(e.get("text") or e.get("message") or ""), style="v3.meta.val")
        body.append("\n")
    _panel(body, "Live Timeline")


# ── Phase 8 — War room (auto on HIGH risk) ───────────────────────────────────

def war_room(*, mission: str = "", risk: str = "high", delay_min: float = 0.0,
             blocked_teams: int = 0, root_cause: str = "",
             recovery_actions: Optional[list] = None) -> None:
    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(style="term.failed", no_wrap=True, min_width=14)
    body.add_column(style="v3.meta.val", no_wrap=True)
    body.add_row("Mission", mission or "—")
    body.add_row("Risk", str(risk).upper())
    body.add_row("Delay", f"{delay_min:.0f}m" if delay_min else "—")
    body.add_row("Blocked teams", str(blocked_teams))
    body.add_row("Root cause", root_cause or "investigating")
    pieces = [body]
    if recovery_actions:
        ra = Text("\n  Recovery actions:\n", style="log.warn")
        for a in recovery_actions[:6]:
            ra.append(f"    → {a}\n", style="v3.meta.val")
        pieces.append(ra)
    console.print()
    _print_panel(Panel(
        Group(*pieces),
        title=Text.assemble(("⚠ ", "term.failed"), ("INCIDENT MODE — WAR ROOM", "term.failed")),
        title_align="left", border_style="term.failed", padding=(1, 2),
        expand=False, box=rich.box.DOUBLE_EDGE))


# ── Phase 9 — Economic view ──────────────────────────────────────────────────

def economics_view(*, tokens_used: int = 0, tokens_remaining: int = 0,
                   llm_calls: int = 0, tool_calls: int = 0, workers: int = 0,
                   elapsed_min: float = 0.0, remaining_min: float = 0.0,
                   deliverables: int = 0) -> None:
    total = tokens_used + tokens_remaining
    budget_health = (1 - tokens_used / total) if total else 1.0
    efficiency = round(deliverables / (tokens_used / 1000), 2) if tokens_used and deliverables else 0.0
    cost_per = round(tokens_used / deliverables) if deliverables else tokens_used
    g = Table.grid(padding=(0, 4), expand=False)
    g.add_column(style="v3.meta.key", no_wrap=True, min_width=18)
    g.add_column(style="v3.meta.val", no_wrap=True)
    g.add_row("Tokens used", f"{tokens_used:,}")
    g.add_row("Tokens remaining", f"{tokens_remaining:,}")
    g.add_row("LLM calls", str(llm_calls))
    g.add_row("Tool calls", str(tool_calls))
    g.add_row("Workers", str(workers))
    g.add_row("Elapsed", f"{elapsed_min:.0f}m")
    g.add_row("Remaining est", f"{remaining_min:.0f}m")
    g.add_row(Text("Budget health", style="v3.meta.key"),
              Text(f"{budget_health*100:.0f}%", style=_risk_style(
                  "low" if budget_health > 0.3 else "high")))
    g.add_row("Efficiency", f"{efficiency} deliv/1k tok")
    g.add_row("Cost / deliverable", f"{cost_per:,} tok")
    _panel(g, "Economics")


# ── Phase 10 — Digital twin ──────────────────────────────────────────────────

def digital_twin(departments: list) -> None:
    if not departments:
        return
    body = Text()
    body.append("  Executive\n", style="v3.card.title")
    for d in departments[:12]:
        util = d.get("utilization", 0)
        pct = util * 100 if util <= 1 else util
        health = "v3.step.done" if pct < 90 else "log.warn"
        body.append("  ├ ", style="v3.duration")
        body.append(f"{d.get('name')} ", style="v3.meta.val")
        body.append(f"({d.get('workload', 0)})", style="v3.duration")
        body.append(f"  {_bar(pct, 6)} {pct:.0f}%", style=health)
        body.append("\n")
    _panel(body, "Digital Twin")


# ── Phase 11 — Agent drill-down (hidden by default) ──────────────────────────

def agent_detail(agent: dict) -> None:
    if not agent:
        return
    g = Table.grid(padding=(0, 3), expand=False)
    g.add_column(style="v3.meta.key", no_wrap=True, min_width=14)
    g.add_column(style="v3.meta.val")
    g.add_row("Agent", str(agent.get("name") or agent.get("id")))
    g.add_row("Current task", str(agent.get("task") or agent.get("step") or "—"))
    g.add_row("Files owned", ", ".join(agent.get("owned_files", [])) or "—")
    g.add_row("Dependencies", ", ".join(agent.get("dependencies", [])) or "—")
    g.add_row("Progress", f"{agent.get('progress', 0):.0f}%")
    g.add_row("Runtime", str(agent.get("runtime", "—")))
    _panel(g, f"Agent — {agent.get('name') or agent.get('id')}")


# ── Command center (Phase 13) ────────────────────────────────────────────────

# View name → one-line description. Used by the /ops command center menu.
COMMAND_VIEWS = {
    "portfolio": "Mission portfolio + progress",
    "organization": "Department / team org chart",
    "teams": "Team utilization + capacity",
    "dependencies": "Dependency radar / bottlenecks",
    "timeline": "Live event timeline",
    "decisions": "Executive decision feed",
    "warroom": "Incident mode (auto on HIGH risk)",
    "economics": "Cost / token / efficiency view",
    "digital-twin": "Live organization twin",
    "replay": "Replay missions / decisions",
    "agents": "Agent drill-down (hidden by default)",
    "logs": "Raw execution logs",
    "files": "File ownership map",
}

# Information hierarchy (Phase 12): descend into detail only when needed.
INFO_HIERARCHY = ("business", "portfolio", "department", "team", "agent", "logs")


def _panel(renderable, title: str) -> None:
    console.print()
    _print_panel(Panel(
        renderable,
        title=Text.assemble((CORE, "kryth.core"), (f"  {title}", "kryth.core")),
        title_align="left", border_style="v3.card.border", padding=(0, 2),
        expand=False, box=rich.box.ROUNDED))


# ── V4 Organizational Dashboard ───────────────────────────────────────────────
#
# Replaces the DAG-centric view with a full mission org view:
#   Mission → Milestones → Deliverables → Critical Path
#   Planner → Team Leads → Workers → Approvals → Blockers
#
# Called after MilestoneEngine.run() completes (final dashboard) and after
# each milestone completes (live progress update).

def v4_milestone_progress(milestone_name: str, ms_result) -> None:
    """Live update after each milestone completes — called by on_milestone_complete."""
    try:
        body = Table.grid(padding=(0, 3), expand=False)
        body.add_column(no_wrap=True, min_width=24)
        body.add_column(no_wrap=True)

        approved = getattr(ms_result, "approved", False)
        success  = getattr(ms_result, "success", False)
        d_done   = getattr(ms_result, "deliverables_completed", 0)
        d_plan   = getattr(ms_result, "deliverables_planned", 0)
        elapsed  = getattr(ms_result, "elapsed_s", 0.0)
        is_crit  = getattr(ms_result, "is_critical", False)
        tl_appr  = getattr(ms_result, "team_lead_approved_count", 0)
        rework   = getattr(ms_result, "rework_notes", "")

        status_style = "v3.step.done" if (success and approved) else ("log.warn" if success else "term.failed")
        status_text  = "APPROVED" if approved else ("REWORK" if success else "FAILED")

        name_display = f"{'⚑ ' if is_crit else ''}{milestone_name}"
        body.add_row(Text(name_display, style="bold bright_cyan" if is_crit else "bold white"),
                     Text(status_text, style=status_style))
        body.add_row(Text("  Deliverables", style="v3.meta.key"),
                     Text(f"{d_done}/{d_plan}", style="v3.meta.val"))
        body.add_row(Text("  Team Lead approvals", style="v3.meta.key"),
                     Text(str(tl_appr), style="v3.meta.val"))
        body.add_row(Text("  Duration", style="v3.meta.key"),
                     Text(f"{elapsed:.1f}s", style="v3.duration"))
        if rework and not approved:
            body.add_row(Text("  Rework notes", style="log.warn"),
                         Text(rework[:60], style="dim"))

        validations = getattr(ms_result, "contract_validations", {})
        if validations:
            body.add_row(Text("  Contracts", style="v3.meta.key"), Text(""))
            for mod, val in list(validations.items())[:6]:
                icon  = "✓" if getattr(val, "passed", False) else "✗"
                score = f"{getattr(val, 'score', 0):.0%}"
                style = "v3.step.done" if getattr(val, "passed", False) else "term.failed"
                body.add_row(Text(f"    {icon} {mod[:22]}", style=style),
                             Text(score, style="dim"))

        _panel(body, f"MILESTONE  {milestone_name[:40]}")
    except Exception:
        pass


def v4_org_dashboard(mission_result, plan=None) -> None:
    """Final V4 organizational dashboard after mission completes.

    Shows the full org hierarchy:
      Mission → Milestones → Deliverables → Critical Path
      Approvals → Blockers → Success Metrics (Phase 10)
    """
    try:
        from rich.console import Group as _Group

        milestones   = getattr(mission_result, "milestones", [])
        project_name = getattr(mission_result, "project_name", "Mission")
        elapsed      = getattr(mission_result, "elapsed_s", 0.0)
        cp_names     = getattr(mission_result, "critical_path_names", [])
        tl_total     = getattr(mission_result, "team_lead_approvals", 0)
        pl_total     = getattr(mission_result, "planner_approvals", 0)
        d_planned    = getattr(mission_result, "total_deliverables_planned", 0)
        d_completed  = getattr(mission_result, "total_deliverables_completed", 0)
        cp_dur       = getattr(mission_result, "critical_path_duration_s", 0.0)
        recovered    = getattr(mission_result, "recovered_from_checkpoint", False)

        ms_done   = sum(1 for ms in milestones if getattr(ms, "success", False))
        ms_appr   = sum(1 for ms in milestones if getattr(ms, "approved", False))
        ms_failed = [ms for ms in milestones if not getattr(ms, "success", False)]

        overall_ok   = mission_result.success if hasattr(mission_result, "success") else ms_done == len(milestones)
        header_style = "v3.step.done" if overall_ok else "term.failed"
        header_label = "MISSION COMPLETE" if overall_ok else "MISSION INCOMPLETE"

        head = Text()
        head.append(f"  {project_name}\n", style="bold white")
        head.append(f"  {header_label}  ", style=f"bold {header_style}")
        head.append(f"  {elapsed:.1f}s total", style="v3.duration")
        if recovered:
            head.append("  [resumed from checkpoint]", style="dim yellow")
        head.append("\n")

        ms_body = Text()
        ms_body.append("\n  MILESTONES\n", style="bold dim")
        for ms in milestones:
            success  = getattr(ms, "success", False)
            approved = getattr(ms, "approved", False)
            is_crit  = getattr(ms, "is_critical", False)
            d_done   = getattr(ms, "deliverables_completed", 0)
            d_plan   = getattr(ms, "deliverables_planned", 0)
            icon     = "✓" if (success and approved) else ("~" if success else "✗")
            crit_m   = " ⚑" if is_crit else ""
            appr_m   = " [approved]" if approved else " [rework]" if success else " [failed]"
            style    = "v3.step.done" if (success and approved) else ("log.warn" if success else "term.failed")
            ms_body.append(f"  {icon} ", style=style)
            ms_body.append(f"{ms.milestone_name}{crit_m}", style="bold bright_cyan" if is_crit else "white")
            ms_body.append(f"  {d_done}/{d_plan} deliverables", style="dim")
            ms_body.append(appr_m + "\n", style=style)

            validations = getattr(ms, "contract_validations", {})
            tl_reviews  = getattr(ms, "team_lead_reviews", {})
            for mod, val in list(validations.items())[:4]:
                v_ok   = getattr(val, "passed", False)
                tl_ok  = getattr(tl_reviews.get(mod, None), "approved", v_ok)
                v_icon = "✓" if v_ok else "✗"
                t_icon = "✓" if tl_ok else "✗"
                ms_body.append(
                    f"      {v_icon} contract  {t_icon} team-lead  {mod[:28]}\n",
                    style="dim" if (v_ok and tl_ok) else "log.warn",
                )

        cp_text = Text()
        if cp_names:
            cp_text.append("\n  CRITICAL PATH\n", style="bold dim")
            cp_text.append("  " + " → ".join(cp_names[:8]), style="yellow")
            if len(cp_names) > 8:
                cp_text.append(f" (+{len(cp_names)-8} more)", style="dim")
            if cp_dur:
                cp_text.append(f"  ({cp_dur:.1f}s)\n", style="v3.duration")
            else:
                cp_text.append("\n")

        blocker_text = Text()
        if ms_failed:
            blocker_text.append("\n  BLOCKERS\n", style="bold term.failed")
            for ms in ms_failed[:4]:
                notes = getattr(ms, "rework_notes", "") or ""
                blocker_text.append(f"  ✗ {ms.milestone_name[:40]}", style="term.failed")
                if notes:
                    blocker_text.append(f"  — {notes[:50]}", style="dim")
                blocker_text.append("\n")

        metrics = Table.grid(padding=(0, 4), expand=False)
        metrics.add_column(style="v3.meta.key", no_wrap=True, min_width=26)
        metrics.add_column(style="v3.meta.val", no_wrap=True)
        metrics.add_row("", "")
        metrics.add_row("METRICS", "")
        metrics.add_row("  Milestones completed",    f"{ms_done}/{len(milestones)}")
        metrics.add_row("  Milestones approved",     f"{ms_appr}/{len(milestones)}")
        metrics.add_row("  Deliverables produced",   f"{d_completed}/{d_planned}")
        metrics.add_row("  Team Lead approvals",     str(tl_total))
        metrics.add_row("  Planner approvals",       str(pl_total))
        if cp_dur:
            metrics.add_row("  Critical path duration", f"{cp_dur:.1f}s")
        metrics.add_row("  Mission duration",        f"{elapsed:.1f}s")
        if recovered:
            metrics.add_row("  Recovery",             "resumed from checkpoint")

        renderable = _Group(head, ms_body, cp_text, blocker_text, metrics)
        console.print()
        _print_panel(Panel(
            renderable,
            title=Text.assemble((CORE, "kryth.core"), ("  V4 ORGANIZATIONAL RUNTIME", "kryth.core")),
            title_align="left",
            border_style="mission.border",
            padding=(1, 2),
            expand=False,
            box=rich.box.DOUBLE_EDGE,
        ))
    except Exception:
        pass


# ── Ponytail Phase 9 — UI & reporting ─────────────────────────────────────────
#
# Surfaces what the Ponytail worker execution profile actually saved this
# mission: files reused/avoided, abstractions avoided, dependencies reused,
# complexity saved, and the overengineering score. Display-only — changes
# nothing about execution, planning, or orchestration.

def ponytail_report(
    *,
    files_created: int = 0,
    files_reused: int = 0,
    files_avoided: int = 0,
    abstractions_avoided: int = 0,
    dependencies_reused: int = 0,
    overengineering_score: float = 0.0,
    grade: str = "lean",
    complexity_added: Optional[list] = None,
    complexity_avoided: Optional[list] = None,
) -> None:
    """Render the Ponytail execution summary panel.

    Called from mission summary / execution summary when the active
    execution profile was PONYTAIL for this mission.
    """
    try:
        grade_style = {
            "lean": "v3.step.done", "acceptable": "v3.step.done",
            "bloated": "log.warn", "overengineered": "term.failed",
        }.get(grade, "v3.meta.val")

        body = Table.grid(padding=(0, 3), expand=False)
        body.add_column(no_wrap=True, min_width=26)
        body.add_column(no_wrap=True)

        body.add_row(Text("Mode", style="v3.meta.key"), Text("PONYTAIL — lazy senior dev", style="bold bright_cyan"))
        body.add_row(Text("Files created", style="v3.meta.key"), Text(str(files_created), style="v3.meta.val"))
        body.add_row(Text("Files reused", style="v3.meta.key"), Text(str(files_reused), style="v3.step.done"))
        body.add_row(Text("Files avoided", style="v3.meta.key"), Text(str(files_avoided), style="v3.step.done"))
        body.add_row(Text("Abstractions avoided", style="v3.meta.key"), Text(str(abstractions_avoided), style="v3.step.done"))
        body.add_row(Text("Dependencies reused", style="v3.meta.key"), Text(str(dependencies_reused), style="v3.step.done"))
        body.add_row(
            Text("Overengineering score", style="v3.meta.key"),
            Text(f"{overengineering_score:.0f}/100  ({grade})", style=grade_style),
        )

        if complexity_avoided:
            body.add_row(Text(""), Text(""))
            body.add_row(Text("Complexity avoided", style="bold v3.step.done"), Text(""))
            for item in complexity_avoided[:5]:
                body.add_row(Text(f"  · {item[:50]}", style="dim"), Text(""))

        if complexity_added:
            body.add_row(Text(""), Text(""))
            body.add_row(Text("Complexity added", style="bold log.warn"), Text(""))
            for item in complexity_added[:5]:
                body.add_row(Text(f"  · {item[:50]}", style="dim"), Text(""))

        _panel(body, "PONYTAIL EXECUTION SUMMARY")
    except Exception:
        pass


def ponytail_mode_banner() -> None:
    """One-line 'Ponytail Mode Active' banner — shown at mission start."""
    try:
        console.print()
        console.print(
            "  [bold bright_cyan]◈ PONYTAIL MODE ACTIVE[/bold bright_cyan]  "
            "[dim]lazy senior dev — stdlib first, fewest files, no unrequested abstractions[/dim]"
        )
    except Exception:
        pass


# ── Phase 12 — Provider Health panel ───────────────────────────────────────────

def provider_health_view() -> None:
    """Display provider health metrics: Provider, Status, Timeouts, Retries, Success Rate."""
    try:
        from agent.production.reliability import _provider_health
        if _provider_health is None:
            return
        metrics = _provider_health.all_providers()
        if not metrics:
            return
        body = Table.grid(padding=(0, 2), expand=False)
        body.add_column(no_wrap=True)
        body.add_column(no_wrap=True, min_width=10)
        body.add_column(no_wrap=True, min_width=8)
        body.add_column(no_wrap=True, min_width=8)
        body.add_column(no_wrap=True, min_width=12)
        for prov, m in metrics.items():
            # Determine status based on success rate and recent errors
            if m.total_requests < 10:
                status = "healthy"  # Not enough data
            elif m.success_rate >= 0.95:
                status = "healthy"
            elif m.success_rate >= 0.8:
                status = "degraded"
            else:
                status = "unhealthy"
            status_style = {"healthy": "v3.step.done", "degraded": "log.warn", "unhealthy": "term.failed"}.get(status, "v3.meta.val")
            body.add_row(
                Text(str(prov), style="v3.meta.val"),
                Text(status.upper(), style=status_style),
                Text(f"{m.provider_errors}", style="v3.duration"),
                Text(f"{m.failures - m.provider_errors}", style="v3.duration"),
                Text(f"{m.success_rate*100:.0f}%", style=status_style if m.success_rate >= 0.8 else "term.failed"),
            )
        _panel(body, "Provider Health")
    except Exception:
        pass
