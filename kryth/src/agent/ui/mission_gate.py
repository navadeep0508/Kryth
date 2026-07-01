"""Pre-execution mission gate — mode selection (Phase 0) + mission approval
(Phase 15).

Before any DAG is built, agents spawned, or work assigned, KRYTH recommends an
execution mode and previews the plan, and the *user decides*. Two sequential
gates, both **DAG/SWARM/multi-agent only** — Direct/Fast/Conversation/single-file
work is never gated and executes immediately.

Safety: every interactive entry point is **non-interactive-safe**. When stdin is
not a TTY (tests, pipes, cron) the gate does NOT block — it returns the
estimator's recommendation / auto-approves so headless runs are unchanged. The
whole gate is off unless ``KRYTH_MISSION_GATE`` is set, so default behavior and
the existing suite are untouched. **User choice always wins** over the estimator.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.panels import _print_panel
from agent.ui.theme import CORE


def gate_enabled() -> bool:
    """Master switch — default ON. The DAG/SWARM scope check + non-interactive
    safety apply on top, so headless runs auto-proceed and only interactive
    DAG/SWARM missions actually prompt. Set KRYTH_MISSION_GATE=0 to disable."""
    return os.environ.get("KRYTH_MISSION_GATE", "1").strip().lower() in ("1", "true", "yes", "on")


def _interactive() -> bool:
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        return False


def _stop_live() -> None:
    """Release the terminal before prompting. Rich's Live/spinner owns the
    cursor while active — an input() called underneath it appears frozen and
    swallows keystrokes (same fix the approval gate uses)."""
    try:
        from agent.ui import stop_spinner
        stop_spinner()
    except Exception:
        pass
    try:
        from agent.ui.live_engine import get_engine
        get_engine().ensure_stopped()
    except Exception:
        pass


# ── Phase 0 — execution mode selection ───────────────────────────────────────

def should_offer_mode(recommendation: str, *, exec_mode_override: str = "auto",
                      is_conversational: bool = False) -> bool:
    """Only offer the mode prompt for parallel recommendations, and only when the
    user hasn't already pinned a mode via /mode. Never for direct/conversation."""
    if is_conversational:
        return False
    if (exec_mode_override or "auto") in ("direct", "dag", "swarm"):
        return False                       # user already chose — don't re-ask
    return str(recommendation).lower() in ("dag", "swarm")


def mode_recommendation_panel(est) -> None:
    d = est.to_dict() if hasattr(est, "to_dict") else dict(est or {})
    direct_eta = d.get("seq_time_s", 0) / 60.0
    dag_eta = d.get("dag_time_s", 0) / 60.0
    g = Table.grid(padding=(0, 4), expand=False)
    g.add_column(style="v3.meta.key", no_wrap=True, min_width=18)
    g.add_column(style="v3.meta.val", no_wrap=True)
    g.add_row(Text("Recommended", style="v3.meta.key"),
              Text(str(d.get("recommendation", "auto")).upper(), style="v3.meta.accent"))
    g.add_row("Independent units", str(d.get("independent_units", 1)))
    g.add_row("Expected speedup", f"{d.get('speedup', 1.0):.1f}x")
    g.add_row("Estimated agents", str(d.get("agents", 1)))
    g.add_row("Direct ETA", f"{direct_eta:.0f}m" if direct_eta else "—")
    g.add_row("DAG ETA", f"{dag_eta:.0f}m" if dag_eta else "—")
    g.add_row("Risk", str(_risk(d)).upper())
    console.print()
    _print_panel(Panel(
        g, title=Text.assemble((CORE, "kryth.core"), ("  EXECUTION MODE RECOMMENDATION", "kryth.core")),
        title_align="left", border_style="mission.border", padding=(1, 2),
        expand=False, box=rich.box.DOUBLE_EDGE))
    console.print(Text("  [D] Direct   [G] DAG   [S] Swarm   [A] Auto (Recommended)",
                       style="v3.meta.val"))


_MODE_KEYS = {"d": "direct", "g": "dag", "s": "swarm", "a": "auto"}


def select_mode(est, *, interactive: Optional[bool] = None,
                reader=None) -> str:
    """Return the chosen execution mode. Non-interactive → the estimator
    recommendation (auto), never blocking. ``reader`` injects input for tests."""
    rec = (est.to_dict()["recommendation"] if hasattr(est, "to_dict")
           else dict(est or {}).get("recommendation", "auto"))
    if interactive is None:
        interactive = _interactive()
    if not interactive:
        return rec
    if reader is None:
        _stop_live()
    mode_recommendation_panel(est)
    read = reader or (lambda: input("  mode > ").strip().lower())
    try:
        choice = read()
    except (EOFError, KeyboardInterrupt):
        return rec
    key = (choice or "a")[:1]
    chosen = _MODE_KEYS.get(key, "auto")
    return rec if chosen == "auto" else chosen


# ── Phase 15 — pre-execution mission approval ────────────────────────────────

@dataclass
class MissionPreview:
    mode: str = "dag"
    eta_min: float = 0.0
    agents: int = 1
    departments: int = 0
    files_create: int = 0
    files_modify: int = 0
    commands: int = 0
    risk: str = "low"
    plan: Dict[str, List[str]] = field(default_factory=dict)   # team -> tasks

    def to_dict(self) -> dict:
        return {"mode": self.mode, "eta_min": round(self.eta_min, 1), "agents": self.agents,
                "departments": self.departments, "files_create": self.files_create,
                "files_modify": self.files_modify, "commands": self.commands,
                "risk": self.risk, "plan": dict(self.plan)}


def _risk(d: dict) -> str:
    rec = str(d.get("recommendation", "")).lower()
    units = d.get("independent_units", 1)
    if rec == "swarm" or units >= 8:
        return "medium"
    return "low"


# Component domain → team label + a default task list seed.
_DOMAIN_TEAM = {
    "frontend": "Frontend Team", "backend": "Backend Team", "database": "Database Team",
    "auth": "Auth Team", "payments": "Payments Team", "tests": "Testing Team",
    "docs": "Documentation Team", "deploy": "Deployment Team",
}


def preview_from_estimate(est, mode: Optional[str] = None) -> MissionPreview:
    """Derive a mission preview from a MissionEstimate. Files/commands are
    estimates for the *plan summary* — not a commitment, just what to expect."""
    d = est.to_dict() if hasattr(est, "to_dict") else dict(est or {})
    components = d.get("components", []) or []
    sections = d.get("sections", []) or []
    plan: Dict[str, List[str]] = {}
    for c in components:
        team = _DOMAIN_TEAM.get(c, f"{c.title()} Team")
        plan.setdefault(team, [])
    # Sections become frontend tasks (the multi-stream signal).
    if sections:
        plan.setdefault("Frontend Team", [])
        plan["Frontend Team"].extend(s.title() for s in sections)
    # Seed each empty team with a representative task so the plan isn't blank.
    for c in components:
        team = _DOMAIN_TEAM.get(c, f"{c.title()} Team")
        if not plan[team]:
            plan[team].append(f"{c.title()} implementation")
    files = d.get("files", 0)
    return MissionPreview(
        mode=mode or d.get("recommendation", "dag"),
        eta_min=d.get("dag_time_s", 0) / 60.0,
        agents=d.get("agents", 1),
        departments=len(plan),
        files_create=files,
        files_modify=max(0, files // 3),
        commands=2 + len([c for c in components if c in ("tests", "deploy")]),
        risk=_risk(d), plan=plan,
    )


def mission_preview_panel(preview: MissionPreview) -> None:
    d = preview.to_dict()
    head = Table.grid(padding=(0, 4), expand=False)
    head.add_column(style="v3.meta.key", no_wrap=True, min_width=16)
    head.add_column(style="v3.meta.val", no_wrap=True)
    head.add_row("Mode", str(d["mode"]).upper())
    head.add_row("Estimated time", f"{d['eta_min']:.0f}m")
    head.add_row("Agents", str(d["agents"]))
    head.add_row("Departments", str(d["departments"]))
    head.add_row("Files to create", str(d["files_create"]))
    head.add_row("Files to modify", str(d["files_modify"]))
    head.add_row("Commands to run", str(d["commands"]))
    head.add_row(Text("Risk", style="v3.meta.key"),
                 Text(str(d["risk"]).upper(),
                      style={"low": "v3.step.done", "medium": "log.warn",
                             "high": "term.failed"}.get(d["risk"], "v3.meta.val")))
    plan = Text("\n  Execution plan:\n", style="v3.duration")
    for team, tasks in d["plan"].items():
        plan.append(f"\n  {team}\n", style="v3.card.title")
        for t in tasks[:8]:
            plan.append(f"    • {t}\n", style="v3.meta.val")
    console.print()
    _print_panel(Panel(
        Group(head, plan),
        title=Text.assemble((CORE, "kryth.core"), ("  MISSION EXECUTION PREVIEW", "kryth.core")),
        title_align="left", border_style="mission.border", padding=(1, 2),
        expand=False, box=rich.box.DOUBLE_EDGE))
    console.print(Text(
        "  [Y] Approve   [D] Direct   [G] DAG   [S] Swarm   [P] Set default   [M] Modify   [N] Cancel",
        style="v3.meta.val"))


_APPROVAL_KEYS = {"y": "approve", "m": "modify", "d": "direct", "n": "cancel"}
# Single-screen decision keys — the Mission Execution Preview is the ONE and ONLY
# execution-strategy decision (no later "use multiple agents?" prompt).
_DECISION_KEYS = {
    "y": "approve", "d": "direct", "g": "dag", "s": "swarm",
    "p": "prefer", "m": "modify", "n": "cancel",
    "e": "edit_plan", "r": "regen_plan",   # V2: plan review options
}


# ── V2: Plan-aware preview ────────────────────────────────────────────────────

def plan_review_panel(project_plan) -> None:
    """V2: Show the full Planner-generated project plan before execution.

    Replaces the estimator-based preview when a ProjectPlan is available.
    Shows: project name, goal, tech stack, modules with dependencies,
    estimated files, recommended mode.
    """
    # Orchestration project planner removed — display basic info only
    pass

    try:
        console.print(Text(
            "  [Y] Execute   [E] Edit Plan   [R] Regenerate   "
            "[D] Force Direct   [G] Force DAG   [S] Force Swarm   [N] Cancel",
            style="v3.meta.val",
        ))
    except Exception:
        pass


def request_plan_decision(
    project_plan,
    *,
    interactive: Optional[bool] = None,
    reader=None,
) -> str:
    """V2: Show plan and get user decision.

    Returns one of:
      approve      → proceed with plan as-is
      edit_plan    → user wants to modify the plan
      regen_plan   → regenerate the plan
      direct       → force single-agent
      dag          → force DAG mode
      swarm        → force SWARM mode
      cancel       → abort
    Non-interactive → 'approve' (headless safe).
    """
    if interactive is None:
        interactive = _interactive()
    if not interactive:
        return "approve"
    if reader is None:
        _stop_live()
    plan_review_panel(project_plan)
    read = reader or (lambda: input("  decision > ").strip().lower())
    try:
        choice = read()
    except (EOFError, KeyboardInterrupt):
        return "cancel"
    return _DECISION_KEYS.get((choice or "y")[:1], "approve")


def request_decision(preview: MissionPreview, *, interactive: Optional[bool] = None,
                     reader=None) -> str:
    """The single execution-strategy decision. Returns one of: approve | direct |
    dag | swarm | prefer | modify | cancel. Non-interactive → 'approve'."""
    if interactive is None:
        interactive = _interactive()
    if not interactive:
        return "approve"
    if reader is None:
        _stop_live()
    mission_preview_panel(preview)
    read = reader or (lambda: input("  decision > ").strip().lower())
    try:
        choice = read()
    except (EOFError, KeyboardInterrupt):
        return "cancel"
    return _DECISION_KEYS.get((choice or "y")[:1], "approve")


def request_approval(preview: MissionPreview, *, interactive: Optional[bool] = None,
                     reader=None) -> str:
    """Return 'approve' | 'modify' | 'direct' | 'cancel'. Non-interactive →
    'approve' (headless runs proceed; the gate is opt-in via env). ``reader``
    injects input for tests."""
    if interactive is None:
        interactive = _interactive()
    if not interactive:
        return "approve"
    if reader is None:
        _stop_live()
    mission_preview_panel(preview)
    read = reader or (lambda: input("  approve > ").strip().lower())
    try:
        choice = read()
    except (EOFError, KeyboardInterrupt):
        return "cancel"
    return _APPROVAL_KEYS.get((choice or "y")[:1], "approve")


# ── Mission contract + critical-action detection ─────────────────────────────

# Actions that are NEVER covered by mission approval — always re-confirmed.
_CRITICAL_PATTERNS = [
    re.compile(r"\b(deploy|release)\b.*\b(prod|production)\b", re.I),
    re.compile(r"\bproduction\b.*\b(deploy|release|push)\b", re.I),
    re.compile(r"\b(drop|delete|truncate)\b.*\b(database|table|schema|db)\b", re.I),
    re.compile(r"\brm\s+-rf\b", re.I),
    re.compile(r"\b(credential|secret|api[_\s-]?key|token|password)s?\b.*\b(change|rotate|delete|update|set|revoke)\b", re.I),
    re.compile(r"\b(change|rotate|delete|update|set|revoke)\b.*\b(credential|secret|api[_\s-]?key|token|password)s?\b", re.I),
    re.compile(r"\b(terraform|kubectl|aws|gcloud|az)\b.*\b(apply|destroy|delete|create)\b", re.I),
    re.compile(r"\bforce.?push\b", re.I),
]


def is_critical(action: str) -> bool:
    """True for production deploys, DB destruction, credential changes, cloud
    infra mutations — Level-3 actions that mission approval never covers."""
    a = action or ""
    return any(p.search(a) for p in _CRITICAL_PATTERNS)


@dataclass
class MissionTeamContract:
    """The IMMUTABLE team organization shown in the Mission Execution Preview.

    This is the single source of truth for execution: the spawner MUST build the
    exact teams (and task ownership) listed here — no legacy templates, no
    orchestrator defaults, no substitutions. ``teams`` maps team label → its
    owned tasks, in the same order the preview displayed them.
    """
    mode: str
    teams: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def team_names(self) -> List[str]:
        return list(self.teams.keys())

    def to_dict(self) -> dict:
        return {"mode": self.mode, "teams": {k: list(v) for k, v in self.teams.items()}}


def team_contract_from_preview(preview: "MissionPreview") -> MissionTeamContract:
    """Freeze the preview's organization into an immutable team contract."""
    return MissionTeamContract(mode=preview.mode,
                               teams={k: list(v) for k, v in preview.plan.items()})


@dataclass
class MissionContract:
    """The standing permission granted by mission approval. Covers approved
    routine actions so the runtime doesn't re-prompt per file; critical actions
    are always excluded (Level 3)."""
    approved: bool = False
    mode: str = "dag"
    allow_writes: bool = True
    allow_edits: bool = True
    allow_commands: bool = True
    approved_layers: int = 0          # number of DAG layers pre-approved

    def covers(self, kind: str, target: str = "") -> bool:
        """True if this routine action is pre-approved (and not critical)."""
        if not self.approved:
            return False
        if is_critical(target) or is_critical(kind):
            return False               # Level 3 — always separate
        return {
            "write": self.allow_writes, "edit": self.allow_edits,
            "command": self.allow_commands,
        }.get(kind, False)

    def to_dict(self) -> dict:
        return {"approved": self.approved, "mode": self.mode,
                "allow_writes": self.allow_writes, "allow_edits": self.allow_edits,
                "allow_commands": self.allow_commands, "approved_layers": self.approved_layers}


# ── Combined gate (used by agent_loop, heavily guarded) ──────────────────────

@dataclass
class GateOutcome:
    proceed: bool          # False → cancel the mission
    mode: str              # resolved execution mode
    orchestrate: bool      # False → run direct (downgrade)
    contract: Optional[MissionContract] = None
    team_contract: Optional[MissionTeamContract] = None   # the approved org (source of truth)
    preapproved: bool = False   # True → orchestration must NOT prompt again
    set_default: bool = False   # True → persist this approval as a session default


def _approved_outcome(mode: str, preview: "MissionPreview",
                      *, set_default: bool = False) -> GateOutcome:
    """Approving DAG/SWARM here is the SINGLE source of truth — mark it
    preapproved so the orchestration pipeline never asks 'use multiple agents?'
    again, and freeze the previewed organization into an immutable team
    contract the spawner MUST honor."""
    return GateOutcome(proceed=True, mode=mode, orchestrate=True,
                       contract=MissionContract(approved=True, mode=mode),
                       team_contract=team_contract_from_preview(
                           preview_from_estimate(preview, mode=mode)
                           if not isinstance(preview, MissionPreview) else preview),
                       preapproved=True, set_default=set_default)


def run_pre_execution_gate(est, *, exec_mode_override: str = "auto",
                           is_conversational: bool = False,
                           interactive: Optional[bool] = None,
                           reader=None, mode_reader=None, approval_reader=None) -> GateOutcome:
    """The ONE execution-strategy decision: show the Mission Execution Preview
    and act on a single keypress. Approving DAG/SWARM is final — orchestration
    launches without a second confirmation.

    ``reader`` injects the single decision input (tests). ``mode_reader`` is
    accepted as a back-compat alias. Never raises into the agent loop.
    """
    rec = (est.to_dict()["recommendation"] if hasattr(est, "to_dict")
           else dict(est or {}).get("recommendation", "direct"))
    reader = reader or mode_reader or approval_reader

    # Scope: only gate parallel recommendations the user hasn't already pinned.
    if not should_offer_mode(rec, exec_mode_override=exec_mode_override,
                             is_conversational=is_conversational):
        mode = exec_mode_override if exec_mode_override in ("direct", "dag", "swarm") else rec
        if mode == "direct":
            return GateOutcome(proceed=True, mode="direct", orchestrate=False)
        return _approved_outcome(mode, preview_from_estimate(est, mode=mode))

    # V2: If a ProjectPlan is attached to the estimator, show the plan preview
    # (with [Y/E/R/D/G/S/N] options) instead of the estimator-based preview.
    _plan = getattr(est, "project_plan", None)
    if _plan is not None:
        decision = request_plan_decision(_plan, interactive=interactive, reader=reader)
        # Map plan decisions to the existing decision vocabulary
        if decision == "edit_plan":
            decision = "modify"
        elif decision == "regen_plan":
            decision = "modify"   # treat as modify; regeneration happens upstream
    else:
        # Single Mission Execution Preview (recommended mode shown).
        preview = preview_from_estimate(est, mode=rec)
        decision = request_decision(preview, interactive=interactive, reader=reader)

    if decision == "cancel":
        return GateOutcome(proceed=False, mode=rec, orchestrate=False)
    if decision in ("direct", "modify"):
        # Direct (or Modify → refine as single-agent) skips orchestration.
        return GateOutcome(proceed=True, mode="direct", orchestrate=False)
    if decision == "dag":
        return _approved_outcome("dag", preview_from_estimate(est, mode="dag"))
    if decision == "swarm":
        return _approved_outcome("swarm", preview_from_estimate(est, mode="swarm"))
    if decision == "prefer":
        # [P] Set default preference: approve recommended AND persist for session.
        return _approved_outcome(rec, preview, set_default=True)
    # approve → execute using the recommended mode
    return _approved_outcome(rec, preview)
