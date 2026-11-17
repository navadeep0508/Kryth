from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.text import Text

from ui.components.args_table import render_args_table
from ui.panels.state import PanelState, PanelStatus


def render_panel(
    state: PanelState, *, expanded: bool = False, width: int = 100
) -> Text | Group:
    """Render one borderless activity row and, only when expanded, its details."""
    line = Text("  ")
    label = _tool_label(state.name)
    line.append(label, style="highlight" if state.status == PanelStatus.RUNNING else "tool")
    if len(label) < 7:
        line.append(" " * (7 - len(label)), style="dim")

    target = _target(state.arguments, expanded=expanded)
    if target:
        line.append("  ", style="dim")
        line.append(target, style="subtitle")

    if state.status == PanelStatus.RUNNING and state.progress:
        line.append("  ", style="dim")
        line.append(state.progress, style="muted")
    elif state.status not in {PanelStatus.RUNNING, PanelStatus.WAITING_APPROVAL}:
        for summary in state.summary:
            if isinstance(summary, Text) and summary.plain:
                line.append("  ", style="dim")
                line.append_text(summary)
                break

    labels = {
        PanelStatus.RUNNING: ("running", "highlight"),
        PanelStatus.WAITING_APPROVAL: ("waiting approval", "warning"),
        PanelStatus.COMPLETED: ("done", "success"),
        PanelStatus.FAILED: ("failed", "error"),
        PanelStatus.CANCELLED: ("cancelled", "warning"),
    }
    label, style = labels[state.status]
    status = Text(f"  {label}", style=style)
    if state.elapsed:
        status.append("  ", style="dim")
        status.append(state.elapsed, style="muted")

    available = max(1, width - status.cell_len)
    if line.cell_len > available:
        if available > 3:
            line.truncate(available - 3, overflow="crop")
            line.append("...", style="dim")
        else:
            line.truncate(available, overflow="crop")
    line.append_text(status)
    line.truncate(max(1, width), overflow="crop")

    if not expanded:
        return line

    detail_blocks: list[Any] = [line]
    if state.status == PanelStatus.RUNNING:
        if state.arguments:
            detail_blocks.append(Text("  ARGUMENTS", style="muted"))
            detail_blocks.append(render_args_table(state.name, state.arguments))
    else:
        if state.details:
            detail_blocks.append(Text("  DETAILS", style="muted"))
            detail_blocks.extend(state.details)
        if state.arguments:
            detail_blocks.append(Text("  ARGUMENTS", style="muted"))
            detail_blocks.append(render_args_table(state.name, state.arguments))
        if state.metadata:
            detail_blocks.append(Text("  METADATA", style="muted"))
            detail_blocks.append(render_args_table(state.name, state.metadata))
    return Group(*detail_blocks)


def _tool_label(name: str) -> str:
    names = {
        "bash": "run",
        "read": "read",
        "write": "write",
        "edit": "edit",
        "grep": "grep",
        "glob": "scan",
        "search": "search",
        "fetch": "fetch",
        "apply_patch": "patch",
        "list_directories": "list",
        "terminal_start": "terminal start",
        "terminal_status": "terminal status",
        "terminal_logs": "terminal logs",
        "terminal_input": "terminal input",
        "terminal_stop": "terminal stop",
        "terminal_restart": "terminal restart",
        "terminal_list": "terminal list",
        "subagent_codebase_investigator": "delegate investigator",
        "subagent_code_reviewer": "delegate reviewer",
        "subagent_software_architect": "delegate architect",
        "subagent_test_writer": "delegate test writer",
        "subagent_debugger": "delegate debugger",
    }
    return names.get(name, name.replace("_", " "))


def _target(arguments: dict[str, Any], *, expanded: bool = False) -> str | None:
    if arguments.get("action") and isinstance(arguments.get("params"), dict):
        action = str(arguments["action"])
        params = arguments["params"]
        target = next(
            (
                str(params[key]).strip().splitlines()[0]
                for key in ("url", "query", "pattern", "selector", "index")
                if params.get(key) is not None and str(params[key]).strip()
            ),
            None,
        )
        return f"{action} {target}" if target else action
    if expanded and isinstance(arguments.get("command"), str):
        return "command details"
    for key in ("path", "url", "command", "name", "pattern", "query"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            target = value.strip().splitlines()[0]
            if key == "command" and len(target) > 56:
                target = target[:53].rstrip() + "..."
            return target
    return None

