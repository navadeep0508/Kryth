"""Human Approval Gate — mandatory checkpoint before multi-agent execution.

Modes (MULTI_AGENT_MODE):
  ASK              — always prompt the user (default)
  AUTO             — accept if parallel_benefit > parallel_cost
  SESSION_APPROVED — user approved for the whole session; skip future prompts
  ALWAYS_SINGLE    — never use parallel; always rebuild as single-agent

The approval is NEVER skipped unless the mode is AUTO or SESSION_APPROVED.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.orchestration.task_dag import TaskDAG
from agent.orchestration.team_generator import AgentRole, TeamPlan
from agent.orchestration.cost_optimizer import CostBenefitAnalysis


class ApprovalMode(str, Enum):
    ASK = "ASK"
    AUTO = "AUTO"
    SESSION_APPROVED = "SESSION_APPROVED"
    ALWAYS_SINGLE = "ALWAYS_SINGLE"


class UserDecision(str, Enum):
    YES = "yes"
    NO = "no"
    ALWAYS = "always"
    SINGLE = "single"


@dataclass
class ApprovalResult:
    approved: bool
    mode_updated: ApprovalMode
    decision: UserDecision
    explanation: str


def _mode_from_env_or_settings() -> ApprovalMode:
    raw = os.getenv("MULTI_AGENT_MODE", "ASK").strip().upper()
    try:
        return ApprovalMode(raw)
    except ValueError:
        return ApprovalMode.ASK


def render_plan(
    dag: TaskDAG,
    team: TeamPlan,
    analysis: CostBenefitAnalysis,
    console: Optional[Console] = None,
) -> None:
    """Render the execution plan to the terminal."""
    if console is None:
        try:
            from agent import ui
            console = ui.get_console()
        except Exception:
            console = Console()

    # Header
    console.print()
    console.rule("[bold #E8FF3A]◈  Orchestration Analysis[/]")
    console.print()

    # Complexity + repo impact
    console.print(f"[bold]Complexity:[/]   {team.complexity:.1f} / 10")
    console.print(f"[bold]Strategy:[/]     {analysis.strategy}")
    console.print(f"[bold]Est. turns:[/]   ~{analysis.estimated_turns}")
    console.print(f"[bold]Est. time:[/]    ~{analysis.estimated_seconds}s")
    console.print()

    # Generated team
    console.print("[bold]Generated Team:[/]")
    for agent in team.agents:
        deps = f"  ← depends on: {', '.join(agent.dependencies)}" if agent.dependencies else ""
        console.print(f"  [cyan]•[/] [bold]{agent.role}[/]{deps}")
        console.print(f"    [dim]{agent.mission}[/]")
    console.print()

    # Benefits and costs
    cols = Table.grid(padding=(0, 4))
    cols.add_column()
    cols.add_column()
    cols.add_row("[bold]Benefits:[/]", "[bold]Costs:[/]")
    cols.add_row(
        "[green]• Isolated ownership per role[/]",
        f"[yellow]• +{analysis.token_overhead:.0%} token overhead[/]",
    )
    if analysis.parallelism_ratio > 0:
        cols.add_row(
            f"[green]• {analysis.parallelism_ratio:.0%} work runs in parallel[/]",
            f"[yellow]• Merge step needed[/]",
        )
    cols.add_row(
        "[green]• Per-role validation[/]",
        f"[yellow]• Risk score: {analysis.risk_score:.1f}/1.0[/]",
    )
    console.print(cols)
    console.print()

    # Task graph
    console.print("[bold]Execution Graph:[/]")
    layers = dag.layers()
    for i, layer in enumerate(layers, 1):
        names = "  ‖  ".join(n.name for n in layer)
        prefix = f"  Layer {i}: " if len(layers) > 1 else "  "
        parallel_hint = " [dim](parallel)[/]" if len(layer) > 1 else ""
        console.print(f"{prefix}[cyan]{names}[/]{parallel_hint}")
    console.print()


def request_approval(
    dag: TaskDAG,
    team: TeamPlan,
    analysis: CostBenefitAnalysis,
    current_mode: ApprovalMode,
    ask_fn: Optional[Callable[[str], str]] = None,
) -> ApprovalResult:
    """Show the execution plan and request approval.

    Returns an ApprovalResult. If `ask_fn` is provided, uses it to read
    the user's response (for testing). Otherwise uses input().
    """
    # Single agent: never needs approval
    if len(team.agents) <= 1 or analysis.strategy == "single":
        return ApprovalResult(
            approved=True,
            mode_updated=current_mode,
            decision=UserDecision.YES,
            explanation="Single agent — no approval needed",
        )

    # ALWAYS_SINGLE: never approve parallel
    if current_mode == ApprovalMode.ALWAYS_SINGLE:
        return ApprovalResult(
            approved=False,
            mode_updated=current_mode,
            decision=UserDecision.SINGLE,
            explanation="ALWAYS_SINGLE mode — rebuilding as single-agent",
        )

    # SESSION_APPROVED: approved by the user earlier this session
    if current_mode == ApprovalMode.SESSION_APPROVED:
        return ApprovalResult(
            approved=True,
            mode_updated=current_mode,
            decision=UserDecision.YES,
            explanation="SESSION_APPROVED — auto-approved",
        )

    # AUTO: approve when benefit > cost
    if current_mode == ApprovalMode.AUTO:
        if team.parallel_benefit > team.parallel_cost:
            return ApprovalResult(
                approved=True,
                mode_updated=current_mode,
                decision=UserDecision.YES,
                explanation=f"AUTO mode — benefit ({team.parallel_benefit:.1f}x) > cost ({team.parallel_cost:.1f}x)",
            )
        else:
            return ApprovalResult(
                approved=False,
                mode_updated=current_mode,
                decision=UserDecision.NO,
                explanation=f"AUTO mode — benefit ({team.parallel_benefit:.1f}x) ≤ cost ({team.parallel_cost:.1f}x)",
            )

    # ASK mode — render plan and prompt
    try:
        from agent import ui
        console = ui.get_console()
    except Exception:
        console = Console()

    render_plan(dag, team, analysis, console)

    console.print("[bold]Do you want to use multiple agents?[/]")
    console.print("  [bold cyan]\\[Y][/]  Yes — use parallel/sequential agents")
    console.print("  [bold red]\\[N][/]  No — use single-agent mode for this task")
    console.print("  [bold yellow]\\[A][/]  Always — approve for this entire session")
    console.print("  [bold dim]\\[S][/]  Single — always prefer single agent this session")
    console.print()

    try:
        if ask_fn:
            raw = ask_fn("Choice [Y/N/A/S]: ").strip().lower()
        else:
            raw = input("Choice [Y/N/A/S]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        raw = "n"

    if raw in ("y", "yes", ""):
        return ApprovalResult(
            approved=True,
            mode_updated=current_mode,
            decision=UserDecision.YES,
            explanation="User approved multi-agent execution",
        )
    if raw in ("a", "always"):
        return ApprovalResult(
            approved=True,
            mode_updated=ApprovalMode.SESSION_APPROVED,
            decision=UserDecision.ALWAYS,
            explanation="User approved for entire session",
        )
    if raw in ("s", "single"):
        return ApprovalResult(
            approved=False,
            mode_updated=ApprovalMode.ALWAYS_SINGLE,
            decision=UserDecision.SINGLE,
            explanation="User chose single-agent mode for this session",
        )
    # "n" or anything else
    return ApprovalResult(
        approved=False,
        mode_updated=current_mode,
        decision=UserDecision.NO,
        explanation="User declined multi-agent execution",
    )
