"""Per-tool rich result renderers.

Every tool call that produces visible output for the user flows through here.
Each renderer turns the raw string result + args into a premium panel that
communicates the engineering action cleanly without exposing internals.
"""

from __future__ import annotations

import os
import re

import rich.box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.ui.console import LOCK, console
from agent.ui.theme import CORE, DOT, ERROR, WAITING


# ── Language map ─────────────────────────────────────────────────────────────

def _lang_for_path(path: str) -> str:
    try:
        from agent.ui.syntax import lexer_for_path
        lexer = lexer_for_path(path)
    except Exception:
        lexer = "text"
    return {
        "python": "Python",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "jsx": "JSX",
        "tsx": "TSX",
        "css": "CSS",
        "html": "HTML",
        "json": "JSON",
        "markdown": "Markdown",
        "md": "Markdown",
        "yaml": "YAML",
        "toml": "TOML",
        "bash": "Shell",
        "sh": "Shell",
        "rust": "Rust",
        "go": "Go",
        "java": "Java",
        "c": "C",
        "cpp": "C++",
        "csharp": "C#",
        "ruby": "Ruby",
        "php": "PHP",
        "swift": "Swift",
        "kotlin": "Kotlin",
        "text": "Text",
    }.get(lexer, lexer.title() if lexer else "Text")


def _size_str(content: str) -> str:
    b = len(content.encode("utf-8", errors="replace"))
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1024 / 1024:.1f} MB"


def _short_path(path: str) -> str:
    """Return a compact display path (basename or last 2 parts)."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) <= 2:
        return path
    return "/".join(parts[-2:])


# ── Read File ─────────────────────────────────────────────────────────────────

def render_read_file(path: str, content: str) -> None:
    """Show a clean file analysis panel for a read_file result."""
    if not path:
        return
    name = os.path.basename(path) or path
    line_count = content.count("\n") + 1 if content.strip() else 0
    lang = _lang_for_path(path)
    size = _size_str(content)

    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=10)
    body.add_column()

    body.add_row(Text("Language", style="muted"), Text(lang, style="title"))
    body.add_row(Text("Lines", style="muted"), Text(f"{line_count:,}", style="title"))
    body.add_row(Text("Size", style="muted"), Text(size, style="muted"))

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble(("📖 ", ""), (name, "title")),
            title_align="left",
            border_style="divider",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


# ── List Files ────────────────────────────────────────────────────────────────

def render_list_files(directory: str, result: str) -> None:
    """Show file listing panel for list_files / glob results."""
    if result.startswith("(") or not result.strip():
        return

    paths = [p.strip() for p in result.splitlines() if p.strip()]
    if not paths:
        return

    body = Table.grid(padding=(0, 1), expand=False)
    body.add_column()

    cap = 20
    for p in paths[:cap]:
        body.add_row(Text.assemble((CORE, "kryth.core"), ("  ", ""), (_short_path(p), "title")))

    if len(paths) > cap:
        body.add_row(Text(f"  … and {len(paths) - cap} more", style="muted"))

    body.add_row(Text(""))
    body.add_row(Text(f"{len(paths)} file{'s' if len(paths) != 1 else ''}", style="muted"))

    dir_label = os.path.basename(directory.rstrip("/\\")) or directory or "."

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble((CORE, "kryth.core"), ("  Repository Analysis", "title"), ("  ·  ", "muted"), (dir_label, "muted")),
            title_align="left",
            border_style="divider",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


# ── Glob ─────────────────────────────────────────────────────────────────────

def render_glob_result(pattern: str, result: str) -> None:
    """Show glob match panel."""
    if not result.strip() or result.startswith("("):
        return
    paths = [p.strip() for p in result.splitlines() if p.strip()]
    if not paths:
        return
    _render_file_list_panel("File Discovery", pattern, paths, "Pattern")


# ── Grep ─────────────────────────────────────────────────────────────────────

def render_grep_result(pattern: str, result: str) -> None:
    """Show pattern match results."""
    if not result.strip() or "(no matches)" in result:
        return

    files = _unique_files_from_lines(result)
    if not files:
        return

    short_pat = pattern[:50] + ("…" if len(pattern) > 50 else "")

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(overflow="fold")

    body.add_row(Text("Query", style="muted"), Text(short_pat, style="kryth.core"))
    body.add_row(Text(""), Text(""))

    cap = 20
    for f in files[:cap]:
        body.add_row(
            Text(CORE, style="log.success"),
            Text(_short_path(f), style="title"),
        )
    if len(files) > cap:
        body.add_row(Text(""), Text(f"  … +{len(files) - cap} more", style="muted"))

    body.add_row(Text(""), Text(""))
    body.add_row(Text(""), Text(f"{len(files)} match{'es' if len(files) != 1 else ''}", style="muted"))

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble((CORE, "kryth.core"), ("  Pattern Match", "title")),
            title_align="left",
            border_style="divider",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


# ── Search ────────────────────────────────────────────────────────────────────

def render_search_result(query: str, result: str) -> None:
    """Show code search results panel."""
    if not result.strip() or "(no" in result.lower()[:30]:
        return

    files = _unique_files_from_lines(result)
    short_q = query[:50] + ("…" if len(query) > 50 else "")

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(overflow="fold")

    body.add_row(Text("Query", style="muted"), Text(short_q, style="kryth.core"))
    body.add_row(Text(""), Text(""))

    if files:
        cap = 20
        for f in files[:cap]:
            body.add_row(
                Text(CORE, style="log.success"),
                Text(_short_path(f), style="title"),
            )
        if len(files) > cap:
            body.add_row(Text(""), Text(f"  … +{len(files) - cap} more", style="muted"))
        body.add_row(Text(""), Text(""))
        body.add_row(Text(""), Text(f"{len(files)} result{'s' if len(files) != 1 else ''}", style="muted"))
    else:
        # Show top lines of result if no parseable files
        lines = result.strip().splitlines()[:6]
        for ln in lines:
            body.add_row(Text(""), Text(ln[:80], style="muted"))
        body.add_row(Text(""), Text(""))

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble((CORE, "kryth.core"), ("  Code Search", "title")),
            title_align="left",
            border_style="divider",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


# ── Delete confirmation ───────────────────────────────────────────────────────

def render_delete_confirm(path: str) -> None:
    """Show a clean file deletion confirmation."""
    name = os.path.basename(path) or path

    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=10)
    body.add_column()

    body.add_row(Text("File", style="muted"), Text(name, style="title"))
    body.add_row(Text("Status", style="muted"), Text("Deleted", style="log.error"))

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble(("🗑  ", ""), (name, "log.error")),
            title_align="left",
            border_style="log.error",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


# ── Browser session ───────────────────────────────────────────────────────────

def render_browser_session(steps: list[str]) -> None:
    """Show browser automation session panel."""
    if not steps:
        return

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(overflow="fold")

    for step in steps:
        body.add_row(
            Text(CORE, style="log.success"),
            Text(step, style="title"),
        )

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble(("🌐 ", ""), ("Browser Session", "title")),
            title_align="left",
            border_style="browser.border",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


def render_browser_result(result: str) -> None:
    """Parse browser_use_task result and show a session summary panel."""
    if not result.strip():
        return

    # Try to extract meaningful steps from the result
    steps: list[str] = []
    for line in result.strip().splitlines():
        stripped = line.strip()
        if stripped and len(stripped) > 4 and not stripped.startswith("{") and not stripped.startswith("["):
            steps.append(stripped[:80])
        if len(steps) >= 8:
            break

    if steps:
        render_browser_session(steps)


# ── Git summary ───────────────────────────────────────────────────────────────

def render_git_result(result: str) -> None:
    """Show a clean git operation summary panel."""
    if not result.strip():
        return

    modified: list[str] = []
    added: list[str] = []
    deleted: list[str] = []

    for line in result.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("M ") or " M " in stripped or stripped.startswith("modified:"):
            modified.append(stripped.split()[-1] if stripped.split() else stripped)
        elif stripped.startswith("A ") or " A " in stripped or stripped.startswith("new file:"):
            added.append(stripped.split()[-1] if stripped.split() else stripped)
        elif stripped.startswith("D ") or " D " in stripped or stripped.startswith("deleted:"):
            deleted.append(stripped.split()[-1] if stripped.split() else stripped)

    if not (modified or added or deleted):
        return

    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=10)
    body.add_column(overflow="fold")

    if modified:
        body.add_row(
            Text("Modified", style="muted"),
            Text("\n".join(_short_path(p) for p in modified[:10]), style="accent"),
        )
    if added:
        body.add_row(
            Text("Added", style="muted"),
            Text("\n".join(_short_path(p) for p in added[:10]), style="log.success"),
        )
    if deleted:
        body.add_row(
            Text("Deleted", style="muted"),
            Text("\n".join(_short_path(p) for p in deleted[:10]), style="log.error"),
        )

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble((CORE, "kryth.core"), ("  Git", "title")),
            title_align="left",
            border_style="divider",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


# ── Shared helpers ────────────────────────────────────────────────────────────

def _unique_files_from_lines(result: str) -> list[str]:
    """Extract unique file paths from grep/search output.

    Handles two formats:
      - files_with_matches: one path per line
      - content mode: path:line:content
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in result.splitlines():
        line = raw.strip()
        if not line:
            continue
        # Skip lines that are clearly not file paths
        if line.startswith(("(", "#", "=", "-", " ")):
            continue
        # content mode: file:line:text
        if re.match(r"^[^:]+:\d+:", line):
            candidate = line.split(":", 1)[0]
        else:
            candidate = line
        if candidate and candidate not in seen:
            # Basic sanity: must look like a path
            if os.sep in candidate or "/" in candidate or "." in candidate:
                seen.add(candidate)
                out.append(candidate)
    return out


def _render_file_list_panel(section: str, label_value: str, paths: list[str], label_key: str = "Pattern") -> None:
    body = Table.grid(padding=(0, 2), expand=False)
    body.add_column(no_wrap=True, min_width=2)
    body.add_column(overflow="fold")

    if label_value:
        short = label_value[:50] + ("…" if len(label_value) > 50 else "")
        body.add_row(Text(label_key, style="muted"), Text(short, style="kryth.core"))
        body.add_row(Text(""), Text(""))

    cap = 20
    for p in paths[:cap]:
        body.add_row(Text(CORE, style="log.success"), Text(_short_path(p), style="title"))
    if len(paths) > cap:
        body.add_row(Text(""), Text(f"  … +{len(paths) - cap} more", style="muted"))

    body.add_row(Text(""), Text(""))
    body.add_row(Text(""), Text(f"{len(paths)} result{'s' if len(paths) != 1 else ''}", style="muted"))

    with LOCK:
        console.print()
        console.print(Panel(
            body,
            title=Text.assemble((CORE, "kryth.core"), (f"  {section}", "title")),
            title_align="left",
            border_style="divider",
            padding=(0, 2),
            expand=False,
            box=rich.box.ROUNDED,
        ))


# ── Main dispatch ─────────────────────────────────────────────────────────────

def dispatch(tool_name: str, args: dict, result: str) -> None:
    """Route a tool result to the appropriate rich renderer.

    Called from mission_console.on_tool_result for tools in _RICH_RESULT.
    Result is the raw string from the tool. No rendering if result looks like
    an error or is empty.
    """
    if not result:
        return
    # Skip tool error strings (format: "ERROR[CODE]: message")
    r = result.lstrip()
    if r.startswith("ERROR") or r.startswith("(error") or r.startswith("(no "):
        return

    try:
        if tool_name == "read_file":
            render_read_file(args.get("path", ""), result)

        elif tool_name == "list_files":
            render_list_files(args.get("directory", "."), result)

        elif tool_name == "glob":
            render_glob_result(
                args.get("pattern", args.get("glob", "")),
                result,
            )

        elif tool_name in {"grep", "search_code"}:
            pattern = (
                args.get("pattern")
                or args.get("query")
                or args.get("regex", "")
            )
            render_grep_result(pattern, result)

        elif tool_name in {"semantic_search", "fts_search", "ast_search", "search_smart", "graphify_query"}:
            query = (
                args.get("query")
                or args.get("prompt")
                or args.get("symbol", "")
            )
            render_search_result(query, result)

        elif tool_name == "delete_file":
            render_delete_confirm(args.get("path", ""))

        elif tool_name in {"git_op"}:
            render_git_result(result)

        elif tool_name in {"browser_use_task", "browser_login"}:
            render_browser_result(result)

    except Exception:
        pass


__all__ = [
    "dispatch",
    "render_read_file",
    "render_list_files",
    "render_glob_result",
    "render_grep_result",
    "render_search_result",
    "render_delete_confirm",
    "render_browser_session",
    "render_browser_result",
    "render_git_result",
]
