from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from rich.syntax import Syntax
from rich.text import Text

from ui.components.args_table import render_args_table
from ui.components.diff_viewer import diff_viewer
from ui.components.memory import render_memory
from ui.components.plan import render_plan
from ui.format import (
    diff_counts,
    diff_stat_text,
    edit_preview_rows,
    extract_read_code,
    guess_language,
    headline,
    secondary_args,
    split_tool_name,
)
from ui.theme import KRYTH_SYNTAX
from utils.text import truncate_text

TOOL_ICON = "Completed"

MAX_BLOCK_TOKENS = 2400
MAX_DIFF_TOKENS = 4000

Blocks = tuple[list[Any], list[Any]]


@dataclass
class ToolOutcome:
    """Everything a renderer is allowed to know about a finished call."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    output: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    truncated: bool = False
    diff: str | None = None
    exit_code: int | None = None
    model_name: str = ""

    @property
    def path(self) -> str | None:
        """The file the call was about, as the tool reported it."""

        value = self.metadata.get("path")
        return value if isinstance(value, str) else None

    def text(self, key: str) -> str | None:
        value = self.args.get(key)
        return value if isinstance(value, str) else None

    def count(self, key: str) -> int | None:
        value = self.metadata.get(key)
        return value if isinstance(value, int) else None

    def output_block(self, style_name: str = "text") -> Syntax:
        return Syntax(
            truncate_text(self.output, self.model_name, MAX_BLOCK_TOKENS),
            style_name,
            theme=KRYTH_SYNTAX,
            background_color="default",
            word_wrap=True,
        )


def _joined(parts: list[Any]) -> Text | None:
    parts = [str(part) for part in parts if part is not None]
    return Text(" | ".join(parts), style="muted") if parts else None


def _read(outcome: ToolOutcome) -> Blocks:
    if not outcome.path:
        return _fallback(outcome)

    result = extract_read_code(outcome.output)
    start_line, code = result if result else (1, outcome.output)

    shown_start = outcome.metadata.get("shown_start")
    shown_end = outcome.metadata.get("shown_end")
    total_lines = outcome.metadata.get("total_lines")
    if shown_start and shown_end and total_lines:
        summary = Text(f"lines {shown_start}–{shown_end} of {total_lines}", style="muted")
    else:
        summary = Text(f"{len(code.splitlines())} lines", style="muted")

    return [summary], [
        Syntax(
            code,
            guess_language(outcome.path),
            theme=KRYTH_SYNTAX,
            background_color="default",
            line_numbers=True,
            start_line=start_line,
            word_wrap=False,
        )
    ]


def _written(outcome: ToolOutcome) -> Blocks:
    summary: list[Any] = []
    details: list[Any] = []

    diff = outcome.diff or ""
    path = outcome.text("path") or outcome.path or "file"
    added, removed = diff_counts(diff)
    is_new = bool(outcome.metadata.get("is_new_file"))
    line_count = outcome.count("lines")

    if is_new:
        line_count = line_count if line_count is not None else added
        label = f"Created {path} | {line_count} lines"
    else:
        label = f"Updated {path} | +{added} -{removed} lines"
    summary.append(Text(label, style="muted"))

    if diff:
        language = guess_language(path)
        if is_new:
            code_lines = [
                line[1:]
                for line in diff.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            ]
            details.append(diff_viewer(
                truncate_text(diff, outcome.model_name, MAX_DIFF_TOKENS), path
            ))
        else:
            details.append(
                diff_viewer(
                    truncate_text(diff, outcome.model_name, MAX_DIFF_TOKENS), path
                )
            )

    return summary, details


def _patch(outcome: ToolOutcome) -> Blocks:
    """A patch touches several files, so the operation list is the headline."""

    operations = outcome.count("operations")
    files = outcome.count("files")

    headline_text = _joined(
        [
            f"{operations} operations" if operations is not None else None,
            f"{files} files" if files is not None else None,
            "dry run" if outcome.metadata.get("dry_run") else None,
        ]
    )

    summary: list[Any] = []
    if headline_text is not None:
        if outcome.diff:
            headline_text.append(" ┈ ", style="muted")
            headline_text.append_text(diff_stat_text(outcome.diff))
        summary.append(headline_text)

    details: list[Any] = [Text(outcome.output.strip(), style="muted")]

    if outcome.diff:
        details.append(
            diff_viewer(
                truncate_text(outcome.diff, outcome.model_name, MAX_DIFF_TOKENS),
                "multi-file patch",
            )
        )

    return summary, details


def _bash(outcome: ToolOutcome) -> Blocks:
    summary = [
        _joined(
            [
                f"exit {outcome.exit_code}" if outcome.exit_code is not None else None,
                f"{len(outcome.output.splitlines())} lines",
            ]
        )
    ]

    details: list[Any] = []
    command = outcome.text("command")
    if command and command.strip():
        details.append(Text(f"$ {command.strip()}", style="muted"))
    details.append(outcome.output_block())

    return summary, details


def _list_dir(outcome: ToolOutcome) -> Blocks:
    entries = outcome.count("entries")
    summary = [
        _joined(
            [
                outcome.path,
                f"{entries} entries" if entries is not None else None,
            ]
        )
    ]
    return summary, [outcome.output_block()]


def _terminal(outcome: ToolOutcome) -> Blocks:
    """Summarize persistent-terminal actions without repeating their full logs."""
    metadata = outcome.metadata
    session = (
        metadata.get("name")
        or metadata.get("session")
        or outcome.text("name")
    )
    status = metadata.get("status")
    parts: list[Any] = [session]
    if isinstance(status, str) and status:
        parts.append(status.replace("_", " ").title())

    if outcome.name == "terminal_logs":
        lines = metadata.get("returned_lines", len(outcome.output.splitlines()))
        parts.append(f"{lines} lines")
    elif outcome.name == "terminal_list":
        count = outcome.count("count")
        if count is not None:
            parts.append(f"{count} sessions")
    else:
        pid = metadata.get("pid")
        if isinstance(pid, int):
            parts.append(f"pid {pid}")
        ports = metadata.get("ports")
        if isinstance(ports, list) and ports:
            parts.append("ports " + ", ".join(map(str, ports)))

    summary = _joined(parts)
    if outcome.name == "terminal_input":
        detail = []
    else:
        detail = [outcome.output_block()] if outcome.output.strip() else []
    return ([summary] if summary else [], detail)


def _grep(outcome: ToolOutcome) -> Blocks:
    matches = outcome.count("matches")
    searched = outcome.count("files_searched")
    summary = [
        _joined(
            [
                f"{matches} matches" if matches is not None else None,
                f"searched {searched} files" if searched is not None else None,
            ]
        )
    ]
    return summary, [outcome.output_block()]


def _glob(outcome: ToolOutcome) -> Blocks:
    matches = outcome.count("matches")
    summary: list[Any] = []
    if matches is not None:
        summary.append(Text(f"{matches} matches", style="muted"))
    return summary, [outcome.output_block()]


def _search(outcome: ToolOutcome) -> Blocks:
    results = outcome.count("results")
    summary = [
        _joined(
            [
                outcome.text("query"),
                f"{results} results" if results is not None else None,
            ]
        )
    ]
    return summary, [outcome.output_block()]


def _fetch(outcome: ToolOutcome) -> Blocks:
    status_code = outcome.count("status_code")
    length = outcome.count("content_length")
    summary = [
        _joined(
            [
                status_code,
                f"{length} bytes" if length is not None else None,
                outcome.text("url"),
            ]
        )
    ]
    return summary, [outcome.output_block()]


def _plan(outcome: ToolOutcome) -> Blocks:
    summary: list[Any] = []
    completed = outcome.count("completed")
    total = outcome.count("total")
    if completed is not None and total:
        summary.append(Text(f"{completed}/{total} completed", style="muted"))
    if not summary:
        summary.append(Text("Plan updated", style="muted"))
    return summary, [render_plan(outcome.metadata)]


def _memory(outcome: ToolOutcome) -> Blocks:
    action = outcome.metadata.get("action")
    count = outcome.count("count")
    summary = [
        _joined(
            [
                action if isinstance(action, str) else "Memory updated",
                f"{count} stored" if count is not None else None,
            ]
        )
    ]
    return summary, [render_memory(outcome.metadata)]


def _failure(outcome: ToolOutcome) -> Blocks:
    message = (outcome.error or "Tool failed").strip()
    message_lines = message.splitlines() or ["Tool failed"]
    headline = message_lines[0]
    if len(headline) > 160:
        headline = headline[:157].rstrip() + "..."
    category = outcome.metadata.get("error_category")
    summary_text = Text(headline, style="error")
    if isinstance(category, str) and category:
        summary_text.append(f"  ({category.replace('_', ' ').lower()})", style="muted")
    if outcome.exit_code is not None:
        summary_text.append(f"  exit {outcome.exit_code}", style="muted")
    summary: list[Any] = [summary_text]
    details: list[Any] = []
    details_text = "\n".join(message_lines[1:]).strip()
    if outcome.output.strip():
        details_text = "\n".join(part for part in (details_text, outcome.output.strip()) if part)
    if details_text:
        details.append(
            Text(truncate_text(details_text, "", MAX_BLOCK_TOKENS), style="muted")
        )
    return summary, details


def _subagent(outcome: ToolOutcome) -> Blocks:
    """Show the delegated role and outcome before the subagent's full report."""
    _base, variant = split_tool_name(outcome.name)
    lines = outcome.output.splitlines()
    termination = next(
        (line.partition(":")[2].strip() for line in lines if line.startswith("Termination:")),
        None,
    )
    tools_line = next(
        (line.partition(":")[2].strip() for line in lines if line.startswith("Tools called:")),
        "",
    )
    tool_count = len([name for name in tools_line.split(",") if name.strip() and name.strip() != "None"])
    summary = _joined(
        [variant or "Subagent", termination, f"{tool_count} tools" if tool_count else None]
    )
    details = [Text(truncate_text(outcome.output, outcome.model_name, MAX_BLOCK_TOKENS), style="assistant")]
    return ([summary] if summary else [], details)


def _browser(outcome: ToolOutcome) -> Blocks:
    """Render a KRYTH Browser tool outcome."""
    args = outcome.args
    action = args.get("action", "browser")
    subject = str(args.get("url") or args.get("selector") or args.get("ref") or action)
    summary = _joined([f"Browser: {action}", subject])
    details = [
        Text(truncate_text(outcome.output, outcome.model_name, MAX_BLOCK_TOKENS), style="assistant")
    ] if outcome.output.strip() else []
    return [summary], details


def _fallback(outcome: ToolOutcome) -> Blocks:
    args = outcome.args
    subject = next((str(args[key]) for key in ("query", "path", "url", "command", "name", "pattern") if args.get(key)), "")
    lowered = outcome.name.lower()
    categories = (
        (("python", "execute_python", "python_exec"), "Python", {"python"}),
        (("git",), "Git", {"git"}),
        (("lsp", "language_server"), "LSP", {"lsp", "language", "server"}),
        (("ast",), "AST", {"ast"}),
        (("vector", "memory"), "Memory", {"vector", "memory"}),
        (("mcp", "__"), "MCP", {"mcp", "server"}),
        (("vision", "image"), "Vision", {"vision", "image"}),
        (("desktop", "computer"), "Desktop", {"desktop", "computer"}),
    )
    match = next((item for item in categories if any(marker in lowered for marker in item[0])), None)
    if match:
        _markers, category, ignored = match
        action = [part for part in lowered.replace("__", "_").split("_") if part and part not in ignored]
        label = " ".join([category, *action]).strip()
    else:
        label = outcome.name.replace("_", " ").capitalize()
    preview = outcome.output.strip().splitlines()
    count = len(preview)
    summary = f"{label}{f' | {subject}' if subject else ''}"
    if count:
        summary += f" | {count} lines"
    details = [Text(truncate_text(outcome.output, outcome.model_name, MAX_BLOCK_TOKENS), style="code")] if outcome.output.strip() else []
    return [Text(summary, style="muted")], details


RENDERERS: dict[str, Callable[[ToolOutcome], Blocks]] = {
    "read": _read,
    "write": _written,
    "edit": _written,
    "apply_patch": _patch,
    "bash": _bash,
    "list_dir": _list_dir,
    "list_directories": _list_dir,
    "grep": _grep,
    "glob": _glob,
    "search": _search,
    "fetch": _fetch,
    "plan": _plan,
    "memory": _memory,
    "terminal_start": _terminal,
    "terminal_status": _terminal,
    "terminal_logs": _terminal,
    "terminal_input": _terminal,
    "terminal_stop": _terminal,
    "terminal_restart": _terminal,
    "terminal_list": _terminal,
    "browser": _browser,
    "browser_connect": _browser,
    "browser_disconnect": _browser,
    "browser_status": _browser,
}


def register_renderer(name: str, renderer: Callable[[ToolOutcome], Blocks]) -> None:
    """Register a concise renderer for a built-in, MCP, or custom tool."""
    RENDERERS[name] = renderer

# These two render their arguments themselves, in prettier form.
ARGS_TABLE_EXEMPT = {"plan", "memory"}


def tool_blocks(outcome: ToolOutcome) -> Blocks:
    """Split a finished call into what is always shown and what folds away."""

    if not outcome.success:
        summary, details = _failure(outcome)
    else:
        renderer = _subagent if outcome.name.startswith("subagent_") else RENDERERS.get(outcome.name, _fallback)
        summary, details = renderer(outcome)

    summary = [block for block in summary if block is not None]
    details = [block for block in details if block is not None]

    if not summary and not details:
        summary.append(Text("(no output)", style="muted"))
    elif not summary:
        fallback_summary, _ = _fallback(outcome)
        summary.extend(fallback_summary)
    if outcome.truncated:
        summary.append(Text("Tool output was truncated", style="warning"))

    head = headline(outcome.args)
    secondary = secondary_args(outcome.args, head[0] if head else None)
    if secondary and outcome.name not in ARGS_TABLE_EXEMPT:
        details.insert(0, render_args_table(outcome.name, secondary))

    return summary, details


def tool_status(
    success: bool, elapsed: str | None = None, hidden_lines: int | None = None
) -> Text:
    """The right-hand side of a tool header: outcome, duration, what is folded."""

    status = Text()
    status.append("done" if success else "failed", style="success" if success else "error")
    if elapsed:
        status.append(" ")
        status.append(elapsed, style="muted")
    if hidden_lines is not None:
        status.append(" · ", style="dim")
        status.append(f"+{hidden_lines} lines", style="dim")
    return status


def tool_header(
    icon: str,
    icon_style: str,
    name: str,
    head: str | None,
    status: Text,
) -> Text:
    label, variant = split_tool_name(name)
    if name == "edit":
        label = "Update"
    spacing = "  " if icon in {"Completed", "Failed", "Cancelled", "Running", "Queued"} else " "
    left = Text()
    if icon:
        left.append(icon, style=icon_style)
        left.append(spacing, style="tool")
    left.append(label.replace("_", " ").capitalize(), style="tool")
    if variant:
        left.append(": ", style="muted")
        left.append(variant, style="muted")
    if head:
        if name in {"write", "edit"}:
            left.append(f"({head})", style="subtitle")
        else:
            left.append("  ")
            left.append(head, style="subtitle")

    if status.plain:
        left.append("  ", style="dim")
        left.append_text(status)
    return left
