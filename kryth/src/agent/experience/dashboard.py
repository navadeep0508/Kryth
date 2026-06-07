"""Experience Dashboard — renders a rich summary for the user before execution."""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.experience.similar_task import SimilarTaskSearchResult
    from agent.experience.prediction import Prediction


def render_dashboard(
    similar,   # SimilarTaskSearchResult
    prediction,  # Prediction
    console=None,
) -> None:
    """Print the experience dashboard to the terminal."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
        if console is None:
            try:
                from agent import ui
                console = ui.get_console()
            except Exception:
                console = Console()
    except ImportError:
        _plain_dashboard(similar, prediction)
        return

    console.print()
    console.rule("[bold #E8FF3A]◈  Experience Engine[/]")
    console.print()

    # Similarity results
    if similar.matches:
        console.print(f"[bold]Found:[/]         {len(similar.matches)} similar task(s)")
        console.print(f"[bold]Best Match:[/]    {similar.best_match.title[:60]}")
        console.print(f"[bold]Confidence:[/]    {similar.confidence:.0%}")
    else:
        console.print("[dim]No similar tasks found — first time solving this.[/]")
    console.print()

    # Prediction
    p = prediction
    col = "[green]" if p.success_probability >= 0.85 else "[yellow]" if p.success_probability >= 0.60 else "[red]"
    console.print(f"[bold]Predicted Success:[/]  {col}{p.success_probability:.0%}[/]  (confidence {p.confidence:.0%})")
    console.print(f"[bold]Est. Turns:[/]         ~{p.likely_build_time_turns}")
    console.print(f"[bold]Rec. Agent Count:[/]   {p.recommended_agent_count}")

    if p.recommended_workflow:
        console.print(f"[bold]Best Workflow:[/]")
        for step in p.recommended_workflow[:6]:
            console.print(f"  [cyan]→[/] {step}")

    if p.likely_repairs:
        console.print(f"[bold]Known Repairs:[/]     {len(p.likely_repairs)}")
        for r in p.likely_repairs[:3]:
            console.print(f"  [yellow]•[/] {r}")

    if p.risk_factors:
        console.print(f"[bold]Risk Factors:[/]")
        for risk in p.risk_factors:
            console.print(f"  [red]![/] {risk}")

    console.print()
    console.print(f"[dim]{p.reasoning}[/]")
    console.print()


def _plain_dashboard(similar, prediction) -> None:
    p = prediction
    print(f"\n=== Experience Engine ===")
    if similar.matches:
        print(f"Found {len(similar.matches)} similar task(s) | confidence {similar.confidence:.0%}")
        print(f"Best: {similar.best_match.title[:60]}")
    print(f"Predicted success: {p.success_probability:.0%} | Est. turns: {p.likely_build_time_turns}")
    if p.likely_repairs:
        print(f"Known repairs: {len(p.likely_repairs)}")
    print()
