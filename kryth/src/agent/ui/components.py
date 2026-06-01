"""Reusable KRYTH Rich components."""

from __future__ import annotations

import os
from typing import Iterable

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from agent.ui.console import console
from agent.ui.hud import startup_reveal
from agent.ui.motion import gradient_text, status_chip
from agent.ui.theme import CORE, DIVIDER, DOT, ERROR, SEP, TEE, WAITING


def section_header(title: str, style: str = "section.exec") -> None:
    console.print()
    console.print(Rule(title=f"[{style}]{CORE} {title.upper()}[/{style}]", characters=DIVIDER, style="divider", align="left"))


def banner(model: str, base_url: str, skill_count: int, version: str = "1.0") -> None:
    """Display the KRYTH startup banner."""
    try:
        from kryth import __version__ as _xver
    except ImportError:
        _xver = version
    no_key = not os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "") in ("not-set", "not-configured")
    startup_reveal(model=model, base_url=base_url, skill_count=skill_count, version=_xver, no_key=no_key)


def _tool_action(name: str) -> str:
    n = name.lower()
    if n in {"read_file", "list_files"}:
        return "READ"
    if n in {"grep", "glob", "search_code", "semantic_search", "lookup_symbol", "lookup_imports", "lookup_dependents"}:
        return "SEARCH"
    if n in {"write_file"}:
        return "WRITE"
    if n in {"edit_file", "multi_edit"}:
        return "PATCH"
    if n in {"run_command", "run_tests", "run_install"}:
        return "EXEC"
    if n in {"delete_file"}:
        return "DELETE"
    if n in {"todo_write", "todo_read"}:
        return "PLAN"
    return name.replace("_", " ").upper()


def tool_header(name: str, summary: str) -> None:
    """Display tool execution with prominence and visual hierarchy."""
    action = _tool_action(name)
    console.print()

    # Create a more prominent tool header
    if summary:
        console.print(Text.assemble(
            (CORE, "tool.bullet"),
            (" " + action, "tool.name"),
            ("  " + DOT + "  ", "muted"),
            (summary, "tool.arg")
        ))
    else:
        console.print(Text.assemble(
            (CORE, "tool.bullet"),
            (" " + action, "tool.name")
        ))


def tool_result(first_line: str, extra_lines: int, *, error: bool = False) -> None:
    style = "tool.error" if error else "tool.result"
    suffix = f" [muted](+{extra_lines} more)[/muted]" if extra_lines else ""
    console.print(f"  [tool.tee]{TEE}[/tool.tee] [{style}]{first_line}[/{style}]{suffix}")


def tool_error_line(msg: str) -> None:
    console.print(f"  [log.error]{ERROR}[/log.error] [log.error]{msg}[/log.error]")


def tool_status_line(msg: str, style: str = "muted") -> None:
    console.print(f"  [tool.tee]{TEE}[/tool.tee] [{style}]{msg}[/{style}]")


def status_line(parts: Iterable[str]) -> None:
    parts = list(parts)
    if parts:
        console.print(Text.assemble((CORE, "kryth.core"), " ", (SEP.join(parts), "status.label")))


def turn_complete(elapsed: float | None, tokens_in: int, tokens_out: int, tool_calls: int = 0) -> None:
    """Premium completion summary with clear metrics."""
    elapsed_str = f"{elapsed:.1f}s" if elapsed else "?"
    total = tokens_in + tokens_out

    from rich.table import Table as _Table
    body = _Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=12)
    body.add_column(no_wrap=True, style="title")

    # Show metrics in logical order
    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" Duration", "muted")),
        elapsed_str
    )
    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" Tokens", "muted")),
        f"{total:,}  [muted](in {tokens_in:,} / out {tokens_out:,})[/muted]"
    )
    if tool_calls:
        body.add_row(
            Text.assemble((CORE, "kryth.core"), (" Tool Calls", "muted")),
            str(tool_calls)
        )
    body.add_row(
        Text.assemble((CORE, "log.success"), (" Status", "muted")),
        Text("Complete", style="log.success")
    )

    console.print()
    console.print(Panel(
        body,
        title=Text.assemble((CORE, "log.success"), (" Mission Complete", "log.success")),
        title_align="left",
        border_style="log.success",
        padding=(1, 2),
        expand=False,
    ))


def plan_panel(plan: dict) -> None:
    body = Table.grid(padding=(0, 1), expand=False)
    body.add_column(style="muted", no_wrap=True, min_width=8, justify="right")
    body.add_column(overflow="fold")

    for key, label in (("goal", "goal"), ("task_type", "type")):
        value = (plan.get(key) or "").strip()
        if value:
            body.add_row(label, f"[title]{value}[/title]" if key == "goal" else f"[accent]{value}[/accent]")

    files = plan.get("required_files") or []
    if files:
        text = Text()
        for i, item in enumerate(files[:20]):
            if i:
                text.append("\n")
            if isinstance(item, dict):
                text.append(item.get("path", "?"), style="title")
                if item.get("purpose"):
                    text.append(f"  {DOT} {item['purpose']}", style="muted")
            else:
                text.append(str(item), style="title")
        body.add_row("files", text)

    steps = plan.get("execution_steps") or []
    if steps:
        text = Text()
        for i, step in enumerate(steps[:20], start=1):
            if i > 1:
                text.append("\n")
            # Use diamond symbol for each stage with spacing
            text.append(f" {CORE}", style="kryth.core")
            text.append(f"  {step}", style="title")
        body.add_row("stages", text)

    validation = plan.get("validation_steps") or []
    if validation:
        body.add_row("verify", "\n".join(str(v) for v in validation[:10]))

    console.print()
    console.print(Panel(body, title=Text.assemble((CORE, "kryth.core"), (" Plan", "section.plan")), title_align="left", border_style="divider", padding=(1, 2), expand=False))


def plan_prose(text: str) -> None:
    body = Markdown(text) if any(m in text for m in ("#", "*", "`")) else Text(text)
    console.print()
    console.print(Panel(body, title=Text.assemble((CORE, "kryth.core"), (" Plan", "section.plan")), title_align="left", border_style="divider", padding=(1, 2), expand=False))


def shell_header(command: str, timeout: int, note: str | None) -> None:
    """Display shell command execution header."""
    del timeout
    console.print()
    if note:
        console.print(Text.assemble(
            (CORE, "section.shell"),
            (" EXEC", "section.shell"),
            ("  " + DOT + "  ", "muted"),
            (command, "title"),
            ("  " + DOT + "  ", "muted"),
            (note, "muted")
        ))
    else:
        console.print(Text.assemble(
            (CORE, "section.shell"),
            (" EXEC", "section.shell"),
            ("  " + DOT + "  ", "muted"),
            (command, "title")
        ))


_TODO_MARK = {
    "pending": (WAITING, "muted"),
    "in_progress": (CORE, "kryth.core"),
    "completed": (CORE, "log.success"),
}


def todos_panel(items: list[dict]) -> None:
    if not items:
        console.print("[muted](no tasks)[/muted]")
        return
    body = Table.grid(padding=(0, 1), expand=False)
    body.add_column(no_wrap=True)
    body.add_column(overflow="fold")
    for item in items:
        mark, style = _TODO_MARK.get(item["status"], (WAITING, "muted"))
        text = item["text"]
        cell = f"[strike muted]{text}[/strike muted]" if item["status"] == "completed" else f"[{style}]{text}[/{style}]"
        body.add_row(f"[{style}]{mark}[/{style}]", cell)
    console.print()
    console.print(Panel(body, title=Text.assemble((CORE, "kryth.core"), (" Tasks", "section.exec")), title_align="left", border_style="divider", padding=(0, 1), expand=False))


def subagent_open(depth: int, description: str) -> None:
    """Display subagent spawn with clear hierarchy."""
    console.print()
    console.print(Text.assemble(
        (CORE, "section.subagent"),
        (" WORKER", "section.subagent"),
        ("  " + DOT + "  ", "muted"),
        (f"depth {depth}", "muted"),
        ("  " + DOT + "  ", "muted"),
        (description, "title")
    ))


def subagent_close(depth: int) -> None:
    """Display subagent completion."""
    console.print(Text.assemble(
        (CORE, "log.success"),
        (" WORKER COMPLETE", "log.success"),
        ("  " + DOT + "  ", "muted"),
        (f"depth {depth}", "muted")
    ))


_PERMISSION_OPTIONS = (("y", "once"), ("a", "always"), ("n", "deny"))


def _release_existing_live() -> None:
    existing = getattr(console, "_live", None)
    if existing is None:
        return
    try:
        existing.stop()
    except Exception:
        try:
            console.clear_live()
        except Exception:
            pass


def _permission_panel(tool: str, signature: str, selected: int) -> Panel:
    """Premium permission request panel with clear visual hierarchy."""
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=10)
    body.add_column(overflow="fold")

    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" TOOL", "muted")),
        Text(tool, style="title")
    )
    body.add_row(
        Text.assemble((CORE, "kryth.core"), (" REQUEST", "muted")),
        Text(signature, style="muted")
    )

    # Spacer
    body.add_row("", "")

    # Options row with better styling
    options_text = Text()
    for i, (key, label) in enumerate(_PERMISSION_OPTIONS):
        if i > 0:
            options_text.append("    ", style="muted")

        if i == selected:
            options_text.append(f"[{key.upper()}] {label.capitalize()}", style="motion.hot")
        else:
            options_text.append(f"[{key.upper()}] {label.capitalize()}", style="kbd")

    body.add_row("", options_text)

    # Spacer
    body.add_row("", "")

    # Help text
    body.add_row(
        "",
        Text("← → navigate  ·  ↵ confirm  ·  esc deny", style="muted")
    )

    return Panel(
        body,
        title=Text.assemble((WAITING, "log.warn"), (" Action Approval Required", "title")),
        title_align="left",
        border_style="log.warn",
        padding=(1, 2),
        expand=False,
    )


def ask_permission_interactive(tool: str, signature: str) -> str:
    from rich.live import Live
    from agent.ui.keyread import read_key

    _release_existing_live()
    selected = 0
    console.print()
    with Live(_permission_panel(tool, signature, selected), console=console, refresh_per_second=20, transient=False, auto_refresh=False) as live:
        while True:
            try:
                key = read_key()
            except (EOFError, KeyboardInterrupt):
                return "n"
            if key in ("LEFT", "UP"):
                selected = (selected - 1) % len(_PERMISSION_OPTIONS)
            elif key in ("RIGHT", "DOWN", "TAB"):
                selected = (selected + 1) % len(_PERMISSION_OPTIONS)
            elif key == "ENTER":
                return _PERMISSION_OPTIONS[selected][0]
            elif key in ("ESC", "CTRL_C"):
                return "n"
            elif key in ("y", "a", "n"):
                selected = next(i for i, (k, _) in enumerate(_PERMISSION_OPTIONS) if k == key)
                live.update(_permission_panel(tool, signature, selected), refresh=True)
                return key
            live.update(_permission_panel(tool, signature, selected), refresh=True)


def divider() -> None:
    console.print(Rule(style="divider"))


def goodbye() -> None:
    """Premium shutdown message."""
    console.print()
    console.print(Text.assemble(
        (CORE, "kryth.core"),
        (" KRYTH", "title"),
        ("  ", ""),
        ("Session terminated", "muted")
    ), justify="center")
    console.print()


def _short_paths(paths: list[str], cap: int = 3) -> str:
    if not paths:
        return ""
    if len(paths) <= cap:
        return ", ".join(paths)
    return f"{', '.join(paths[:cap])}  [muted](+{len(paths) - cap} more)[/muted]"


def run_summary_panel(s: dict) -> None:
    status = s.get("status", "done")
    turns_used = s.get("turns_used", 0)
    rows: list[tuple[str, str]] = []

    if status == "max_turns":
        rows.append((f"[log.warn]{ERROR} Incomplete[/log.warn]", f"[muted]{turns_used} turns used; reply 'continue' to resume[/muted]"))
    elif status == "interrupted":
        rows.append((f"[log.warn]{ERROR} Interrupted[/log.warn]", "[muted]partial work preserved[/muted]"))
    elif status == "api_error":
        rows.append((f"[log.error]{ERROR} API error[/log.error]", "[muted]turn stopped before completion[/muted]"))

    if s.get("errors") or s.get("denied") or s.get("shell_fail"):
        rows.append((f"[log.error]{ERROR} Issues[/log.error]", f"[log.error]{s.get('errors', 0)} tool · {s.get('shell_fail', 0)} command · {s.get('denied', 0)} blocked[/log.error]"))
    if s.get("files_written"):
        rows.append((f"[log.success]{CORE} Files created[/log.success]", _short_paths(s["files_written"])))
    if s.get("files_edited"):
        rows.append((f"[accent]{CORE} Files modified[/accent]", _short_paths(s["files_edited"])))
    if s.get("files_deleted"):
        rows.append((f"[log.error]{ERROR} Files deleted[/log.error]", _short_paths(s["files_deleted"])))
    if s.get("shell_ok"):
        rows.append((f"[log.success]{CORE} Tests/commands[/log.success]", f"{s['shell_ok']} passed"))
    if s.get("subagent_spawns"):
        rows.append((f"[accent]{CORE} Workers[/accent]", str(s["subagent_spawns"])))

    activity = []
    if s.get("tools_called"):
        activity.append(f"{s['tools_called']} tool calls")
    if s.get("retries"):
        activity.append(f"{s['retries']} retries")
    if s.get("coercions"):
        activity.append(f"{s['coercions']} argument fixes")
    if activity:
        rows.append((f"[muted]{WAITING} Activity[/muted]", "[muted]" + " · ".join(activity) + "[/muted]"))

    if not rows:
        return

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True)
    body.add_column(overflow="fold")
    for left, right in rows:
        body.add_row(left, right)

    title_style = "log.success" if status == "done" else "log.warn"
    title = "Complete" if status == "done" else status.replace("_", " ").title()
    console.print()
    console.print(Panel(body, title=Text.assemble((CORE if status == "done" else ERROR, title_style), (" " + title, title_style)), title_align="left", border_style="divider", padding=(0, 1), expand=False))
