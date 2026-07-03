"""Executive Layer — Mission Dashboard.

Shows ONLY: Mission · Progress · Current Stage · Team Status · Result.
No raw tool calls. No internal reasoning. No implementation details.

Rendered on TURN_END, MISSION_*, and AGENT_UPDATE events.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TextColumn
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


def _bar(percent: int, width: int = 20) -> Text:
    """Smooth filled/empty bar using block characters."""
    filled = round(percent / 100 * width)
    empty = width - filled
    t = Text()
    t.append("█" * filled, style="mission.progress")
    t.append("░" * empty, style="agent.bar.bg")
    t.append(f"  {percent}%", style="mission.stage")
    return t


def render_mission_panel(
    goal: str,
    status: str,
    progress: int,
    stage: str,
    elapsed: str,
    eta: str,
    agents: list,
    summary: dict | None = None,
) -> None:
    """Render the executive mission panel to the console."""
    body = Table.grid(padding=(0, 2), expand=True)
    body.add_column(no_wrap=True, style="muted", min_width=14)
    body.add_column(overflow="fold", ratio=1)

    # Mission goal
    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" Mission", "muted")),
        Text(goal, style="mission.goal"),
    )

    # Progress bar
    bar = _bar(progress)
    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" Progress", "muted")),
        bar,
    )

    # Current activity
    if stage:
        body.add_row(
            Text.assemble((CORE, "kryth.core"), (" Activity", "muted")),
            Text(stage, style="mission.stage"),
        )

    # Timing row
    time_parts = Text()
    if elapsed:
        time_parts.append(f"Elapsed {elapsed}", style="mission.eta")
    if eta:
        time_parts.append(f"  {DOT}  ETA {eta}", style="mission.eta")
    if elapsed or eta:
        body.add_row(
            Text.assemble((CORE, "kryth.core"), (" Time", "muted")),
            time_parts,
        )

    # Agent team table
    if agents:
        agent_table = Table.grid(padding=(0, 1), expand=False)
        agent_table.add_column(min_width=12, no_wrap=True)
        agent_table.add_column(min_width=22, no_wrap=True)
        for a in agents:
            name_style = {
                "running": "agent.running",
                "done": "agent.done",
                "failed": "agent.failed",
            }.get(a.status, "agent.idle")
            agent_bar = _bar(a.progress, width=14)
            name_text = Text(a.name, style=name_style)
            agent_table.add_row(name_text, agent_bar)

        body.add_row(
            Text.assemble((CORE, "kryth.core"), (" Team", "muted")),
            agent_table,
        )

    # Summary rows at completion
    if summary:
        for key, val in list(summary.items())[:6]:
            body.add_row(
                Text(f"  {key}", style="muted"),
                Text(str(val), style="mission.stage"),
            )

    # Status glyph + border
    if status == "complete":
        border = "mission.progress"
        title_glyph = (CORE, "mission.complete")
        title_label = ("  Mission Complete", "mission.complete")
    elif status == "failed":
        border = "mission.failed"
        title_glyph = (ERROR, "mission.failed")
        title_label = ("  Mission Failed", "mission.failed")
    elif status == "running":
        border = "mission.border"
        title_glyph = (CORE, "kryth.core")
        title_label = ("  KRYTH", "title")
    else:
        border = "divider"
        title_glyph = (WAITING, "muted")
        title_label = ("  KRYTH", "muted")

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble(title_glyph, title_label),
        title_align="left",
        border_style=border,
        padding=(1, 2),
        expand=True,
    ))


def render_mission_complete_panel(
    goal: str,
    elapsed: str,
    summary: dict,
    tool_calls: int = 0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """The final success panel — replaces the generic turn_complete."""
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=16)
    body.add_column(overflow="fold")

    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" Goal", "muted")),
        Text(goal, style="mission.goal"),
    )

    # Action items from summary
    actions = summary.get("actions", [])
    if actions:
        acts = Text()
        for i, a in enumerate(actions[:8]):
            if i:
                acts.append("\n")
            acts.append(f"{CORE} {a}", style="eng.done")
        body.add_row(
            Text.assemble((CORE, "kryth.core"), (" Actions", "muted")),
            acts,
        )

    # Key metrics
    for label, val in [
        ("Files Modified", summary.get("files_modified", 0)),
        ("Warnings", summary.get("warnings", 0)),
        ("Critical Errors", summary.get("critical_errors", 0)),
    ]:
        if val is not None:
            style = "log.error" if label == "Critical Errors" and val > 0 else "title"
            body.add_row(Text(f"  {label}", style="muted"), Text(str(val), style=style))

    project_status = summary.get("project_status", "")
    if project_status:
        body.add_row(
            Text.assemble((CORE, "log.success"), (" Project Status", "muted")),
            Text(project_status, style="mission.complete"),
        )

    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" Duration", "muted")),
        Text(elapsed, style="mission.eta"),
    )
    if tokens_in or tokens_out:
        body.add_row(
            Text.assemble((CORE, "kryth.core"), (" Tokens", "muted")),
            Text(f"{tokens_in + tokens_out:,}  in {tokens_in:,} / out {tokens_out:,}",
                 style="muted"),
        )

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "mission.complete"), ("  Mission Complete", "mission.complete")),
        title_align="left",
        border_style="mission.progress",
        padding=(1, 2),
        expand=False,
    ))
