"""Approval UI — Batch approval panel replacing per-tool prompts.

Instead of:
    Write file? [y/n]
    Run npm? [y/n]
    Run dev server? [y/n]

Shows a single plan approval:

    ╭─ Execution Plan ──────────────────────────────────────╮
    │                                                       │
    │  Will perform:                                        │
    │  ◈  Create package.json                              │
    │  ◈  Install dependencies                             │
    │  ◈  Start development server                         │
    │  ◈  Verify browser                                   │
    │                                                       │
    │  Estimated Time    45s                               │
    │  Risk Level        Low                               │
    │                                                       │
    │  [Y] Approve    [N] Deny                             │
    │                                                       │
    ╰───────────────────────────────────────────────────────╯
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.panels import _print_panel
from agent.ui.theme import CORE, DOT, ERROR, WAITING


def render_approval_panel(
    items: list,
    risk: str = "low",
    estimated_time: str = "",
) -> None:
    """Render a batch approval panel (display only — call interactive for input)."""
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=18)
    body.add_column(overflow="fold")

    # Action list
    if items:
        acts = Text()
        for i, item in enumerate(items):
            if i:
                acts.append("\n")
            acts.append(f"{CORE}  {item}", style="approval.action")
        body.add_row(Text("Will perform", style="muted"), acts)

    body.add_row(Text(""), Text(""))  # spacer

    # Metadata
    if estimated_time:
        body.add_row(Text("Estimated Time", style="muted"), Text(estimated_time, style="title"))

    risk_style = {
        "low":    "approval.risk.low",
        "medium": "approval.risk.medium",
        "high":   "approval.risk.high",
    }.get(risk.lower(), "muted")
    body.add_row(Text("Risk Level", style="muted"), Text(risk.title(), style=risk_style))

    body.add_row(Text(""), Text(""))  # spacer

    # Option hints
    options = Text.assemble(
        ("[Y]", "motion.hot"), (" Approve  ", "muted"),
        ("[N]", "kbd"), (" Deny", "muted"),
    )
    body.add_row(Text(""), options)

    _print_panel(Panel(
        body,
        title=Text.assemble((WAITING, "log.warn"), ("  Execution Plan", "title")),
        title_align="left",
        border_style="approval.border",
        padding=(1, 2),
        expand=False,
    ))


def render_approval_interactive(
    items: list,
    risk: str = "low",
    estimated_time: str = "",
) -> str:
    """Render batch approval and block for user input.

    Returns 'y' or 'n'.
    """
    from rich.live import Live
    from agent.ui.keyread import read_key

    render_approval_panel(items, risk, estimated_time)

    console.print()
    console.print("[muted]Approve? [Y/n]: [/muted]", end="")

    try:
        key = read_key()
        if key in ("n", "N", "ESC", "CTRL_C"):
            console.print("N", style="log.error")
            return "n"
        console.print("Y", style="log.success")
        return "y"
    except (EOFError, KeyboardInterrupt):
        return "n"
