"""
KRYTH Terminal Approval UI — professional permission interface for terminal commands.

Provides risk classification, shell detection, smart command descriptions,
and an interactive approval menu with arrow-key navigation.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.syntax import Syntax
from rich.text import Text

from tools.base import ToolConfirmation
from ui.components.args_table import render_args_table
from ui.components.diff_viewer import diff_viewer
from ui.format import guess_language
from ui.theme import KRYTH_SYNTAX


class RiskLevel(str, Enum):
    """Command risk classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ShellType(str, Enum):
    """Detected shell type for command rendering."""

    POWERSHELL = "powershell"
    CMD = "cmd"
    GIT_BASH = "git_bash"
    WSL_BASH = "wsl_bash"
    LINUX_BASH = "linux_bash"
    UNKNOWN = "unknown"


class ApprovalAction(str, Enum):
    """User's decision on an approval request."""

    ALLOW_ONCE = "allow_once"
    ALLOW_SESSION = "allow_session"
    REJECT = "reject"
    CANCELLED = "cancelled"


# --- Risk classification patterns ---

_CRITICAL_RISK = [
    r"\brm\s+-rf\s+/(?:\s|$)", r"\bformat\s+[a-z]:", r"\bdiskpart\b",
    r"\bremove-item\b(?=.*(?:system32|windows|/|\\))(?=.*(?:-recurse|-force))",
    r"\bshutdown\s+/(?:s|r)\b",
]

_HIGH_RISK = [
    r"\brm\b.*-rf", r"\brmdir\b", r"\bdel\b\s", r"\berase\b",
    r"\bremove-item\b.*(?:-recurse|-force)", r"\bdel\s+/s\b", r"\brd\s+/s\b",
    r"\bformat\b\s", r"\bmkfs\b", r"\bfdisk\b", r"\bparted\b",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b",
    r"\bsudo\b", r"\brunas\b", r"\bpsexec\b",
    r"\bchmod\b", r"\bchown\b", r"\bicacls\b",
    r"\bdd\b\s", r"\breg\s+(delete|add)\b",
    r"\bnet\s+(stop|start|user|localgroup)\b",
    r"\bsc\s+(stop|start|delete|config)\b",
    r"\bDrop\s+Table\b", r"\bTRUNCATE\b", r"\bDELETE\s+FROM\b",
]

_MEDIUM_RISK = [
    r"\bnpm\s+(install|uninstall|publish)\b",
    r"\bpip\s+(install|uninstall)\b",
    r"\bcargo\s+(install|uninstall)\b",
    r"\bgo\s+install\b",
    r"\buvicorn\b", r"\bgunicorn\b", r"\bwaitress\b",
    r"\bdjango\s+admin\b", r"\bmanage\.py\s+runserver\b",
    r"\bflask\s+run\b",
    r"\bnpm\s+run\b", r"\bpnpm\s+(run|install)\b", r"\byarn\s+(install|run)\b",
    r"\bnpx\b",
    r"\bgit\s+(commit|push|pull|merge|rebase|reset|checkout)\b",
    r"\bdocker\s+(run|build|rm|rmi|stop|kill)\b",
    r"\bkubectl\s+(apply|delete|run)\b",
    r"\bmvn\s+(install|deploy)\b", r"\bgradle\s+(build|deploy)\b",
    r"\bdotnet\s+(build|publish|run)\b",
    r"\bchoco\s+(install|uninstall)\b", r"\bwinget\s+(install|uninstall)\b",
]

_LOW_RISK = [
    r"\bls\b", r"\bdir\b", r"\bcat\b", r"\btype\b",
    r"\bhead\b", r"\btail\b", r"\bless\b", r"\bmore\b",
    r"\bgrep\b", r"\brg\b", r"\bag\b", r"\bfind\b",
    r"\bgit\s+(status|log|diff|branch|show)\b",
    r"\bpython\s+--version", r"\bnode\s+--version",
    r"\bpwd\b", r"\bwhoami\b", r"\becho\b",
    r"\bdate\b", r"\bhostname\b", r"\bver\b",
]


def classify_risk(command: str) -> RiskLevel:
    """Classify a command's risk level based on patterns.

    High-risk patterns are checked first, then medium, then low.
    Unknown commands default to medium (informational only — never bypasses
    existing permission requirements).
    """
    lowered = command.lower().strip()

    for pattern in _CRITICAL_RISK:
        if re.search(pattern, lowered):
            return RiskLevel.CRITICAL

    for pattern in _HIGH_RISK:
        if re.search(pattern, lowered):
            return RiskLevel.HIGH

    for pattern in _MEDIUM_RISK:
        if re.search(pattern, lowered):
            return RiskLevel.MEDIUM

    for pattern in _LOW_RISK:
        if re.search(pattern, lowered):
            return RiskLevel.LOW

    return RiskLevel.MEDIUM


def detect_shell(command: str, cwd: Path | str | None = None) -> ShellType:
    """Detect the shell type from the command and environment context.

    Never modifies the command string — only identifies the shell for
    display and syntax highlighting purposes.
    """
    lowered = command.lower().strip()
    words = lowered.split()
    first_word = words[0] if words else ""

    # PowerShell cmdlets use Verb-Noun pattern (exactly two lowercase words).
    if re.match(r"^[a-z]+-[a-z]+$", first_word):
        return ShellType.POWERSHELL

    # PowerShell-specific commands and aliases
    ps_cmds = {
        "invoke-webrequest", "invoke-restmethod", "get-process",
        "stop-process", "get-service", "set-executionpolicy",
        "import-module", "export-csv", "convertto-json",
        "get-childitem", "get-content", "set-content",
        "new-item", "remove-item", "copy-item", "move-item",
        "get-command", "get-help", "get-member",
        "where-object", "foreach-object", "select-object",
        "write-host", "write-output", "write-error",
        "test-connection", "test-path",
        "start-process", "stop-process",
        "get-eventlog", "get-wmiobject",
    }
    if first_word in ps_cmds:
        return ShellType.POWERSHELL

    # WSL detection from cwd path
    if cwd:
        cwd_str = str(cwd)
        if "wsl" in cwd_str.lower() or cwd_str.startswith("//wsl$"):
            return ShellType.WSL_BASH

    # CMD-specific commands (not common to bash)
    cmd_cmds = {
        "dir", "cls", "copy", "del", "erase", "ren", "type", "vol",
        "tasklist", "taskkill", "ipconfig", "netstat", "sfc",
        "chkdsk", "diskpart", "reg", "net", "sc", "shutdown",
    }
    if first_word in cmd_cmds:
        return ShellType.CMD

    return ShellType.LINUX_BASH


# Smart command descriptions — informational only, never guesses.
_COMMAND_DESCRIPTIONS = [
    (r"\bls\b", "List files and directories"),
    (r"\bdir\b", "List files and directories"),
    (r"\bcat\b", "Display file contents"),
    (r"\btype\b", "Display file contents"),
    (r"\bhead\b", "Show the beginning of a file"),
    (r"\btail\b", "Show the end of a file"),
    (r"\bwhoami\b", "Show the current user"),
    (r"\bpwd\b", "Show the current working directory"),
    (r"\becho\b", "Print text to the terminal"),
    (r"\bdate\b", "Show the current date and time"),
    (r"\bhostname\b", "Show the computer name"),
    (r"\bver\b", "Show the Windows version"),
    (r"\bpython\s+--version", "Check the Python version"),
    (r"\bnode\s+--version", "Check the Node.js version"),
    (r"\bnpm\s+--version", "Check the npm version"),
    (r"\buvicorn\b", "Start a Python ASGI web server"),
    (r"\bgunicorn\b", "Start a Python WSGI web server"),
    (r"\bdjango", "Run a Django management command"),
    (r"\bflask\s+run", "Run a Flask development server"),
    (r"\bnpm\s+install\b", "Install npm packages"),
    (r"\bpip\s+install\b", "Install Python packages"),
    (r"\bcargo\s+install\b", "Install a Rust crate"),
    (r"\bgit\s+commit\b", "Commit changes to git"),
    (r"\bgit\s+push\b", "Push git changes to a remote"),
    (r"\bgit\s+pull\b", "Pull git changes from a remote"),
    (r"\bdocker\s+run\b", "Run a Docker container"),
    (r"\brm\b", "Remove files or directories"),
    (r"\bmkdir\b", "Create a directory"),
    (r"\btouch\b", "Create or update a file"),
    (r"\bcp\b", "Copy files or directories"),
    (r"\bmv\b", "Move or rename files"),
    (r"\bremove-item\b", "Remove files or directories"),
    (r"\bget-childitem\b", "List files and directories"),
    (r"\brd\b|\brmdir\b", "Remove a directory"),
]


def describe_command(command: str) -> str | None:
    """Return a human-readable description of what the command does, or None.

    Never invents behavior — only returns a description when a known
    pattern is matched.
    """
    lowered = command.lower().strip()
    for pattern, description in _COMMAND_DESCRIPTIONS:
        if re.search(pattern, lowered):
            return description
    return None


# --- Visual mapping ---

RISK_STYLES = {
    RiskLevel.LOW: ("success", "LOW"),
    RiskLevel.MEDIUM: ("warning", "MEDIUM"),
    RiskLevel.HIGH: ("error", "HIGH"),
    RiskLevel.CRITICAL: ("error", "CRITICAL"),
}

SHELL_LABELS = {
    ShellType.POWERSHELL: "PowerShell",
    ShellType.CMD: "Command Prompt",
    ShellType.GIT_BASH: "Git Bash",
    ShellType.WSL_BASH: "WSL Bash",
    ShellType.LINUX_BASH: "Bash",
    ShellType.UNKNOWN: "Unknown",
}

APPROVAL_OPTIONS = [
    ("y", "Allow once", "success", ApprovalAction.ALLOW_ONCE),
    ("a", "Allow for session", "info", ApprovalAction.ALLOW_SESSION),
    ("n", "Reject", "error", ApprovalAction.REJECT),
]


class TerminalApprovalPanel:
    """Compact approval selector shared by terminal and file operations."""

    def __init__(
        self,
        confirmation: ToolConfirmation,
        cwd: Path | str | None = None,
        selected: int = 0,
        *,
        expanded: bool = False,
    ) -> None:
        self.confirmation = confirmation
        self.cwd = Path(cwd) if cwd else None
        self.selected = max(0, min(2, selected))
        self.expanded = expanded

        self.command = confirmation.command or ""
        try:
            self.risk_level = RiskLevel(getattr(confirmation, "risk_level", RiskLevel.MEDIUM))
        except ValueError:
            self.risk_level = RiskLevel.MEDIUM
        if confirmation.is_dangerous and self.risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}:
            self.risk_level = RiskLevel.HIGH
        self.shell_type = getattr(confirmation, "shell_type", ShellType.UNKNOWN)
        self.description = confirmation.description or ""

        self._risk_style, self._risk_label = RISK_STYLES[self.risk_level]

    # -- Rich renderable protocol --

    def __rich_console__(self, console: Any, options: Any) -> Any:
        blocks: list[Any] = [
            Text("APPROVAL REQUIRED", style="bold warning"),
            self._tool_risk(),
            self._description(options.max_width),
            self._menu(),
            self._hints(),
        ]
        if self.expanded:
            blocks.extend(self._details(options.max_width))
        yield Group(*blocks)

    # -- Panel sections --

    def _tool_risk(self) -> Text:
        tool = self.confirmation.tool_name.replace("_", " ").title()
        if self.confirmation.tool_name == "bash":
            tool = SHELL_LABELS.get(self.shell_type, "Shell")
        header = Text(tool, style="primary")
        header.append(" | ")
        header.append(self._risk_label, style=f"bold {self._risk_style}")
        return header

    def _description(self, width: int) -> Text | None:
        summary = describe_command(self.command) if self.command else None
        if summary:
            line = Text(summary, style="secondary")
        elif self.confirmation.diff is not None:
            operation = "Create" if self.confirmation.diff.is_new_file else "Modify"
            path = self.confirmation.diff.path
            if self.cwd is not None:
                try:
                    path = Path(path).resolve().relative_to(self.cwd.resolve())
                except (OSError, ValueError):
                    pass
            line = Text(f"{operation} {path}", style="secondary")
        elif self.command:
            line = Text("Review the command before approving.", style="warning")
        else:
            description = self.description
            for prefix in ("Execute (DANGEROUS): ", "Execute: "):
                if description.startswith(prefix):
                    description = ""
            line = Text(description or "Review the requested operation.", style="secondary")
        line.truncate(max(1, width), overflow="ellipsis")
        return line

    def _context(self) -> Text:
        info = Text()
        if self.confirmation.tool_name == "bash":
            info.append(SHELL_LABELS.get(self.shell_type, "Unknown shell"), style="muted")
            info.append(" in ", style="muted")
            info.append(str(self.cwd) if self.cwd else ".", style="muted")
        elif self.confirmation.diff is not None:
            info.append("Target ", style="muted")
            info.append(str(self.confirmation.diff.path), style="code")
        elif self.confirmation.affected_paths:
            info.append("Target ", style="muted")
            info.append(", ".join(map(str, self.confirmation.affected_paths)), style="code")
        else:
            info.append("Workspace ", style="muted")
            info.append(str(self.cwd) if self.cwd else ".", style="muted")
        return info

    def _menu(self) -> Text:
        menu = Text()
        for i, (key, label, style, _action) in enumerate(APPROVAL_OPTIONS):
            if i > 0:
                menu.append("  ", style="dim")
            is_selected = i == self.selected
            if is_selected:
                menu.append(f"> [{key.upper()}] {label}", style=f"bold {style}")
            else:
                menu.append(f"[{key.upper()}] {label}", style=style)
        return menu

    def _session_scope(self) -> str:
        if self.command:
            return "This KRYTH session: this exact command only."
        return f"This KRYTH session: future {self.confirmation.tool_name} requests."

    def _details(self, width: int) -> tuple[Any, ...]:
        blocks: list[Any] = [Text("DETAILS", style="bold accent")]
        if self.cwd:
            blocks.append(Text(f"Working directory: {self.cwd}", style="muted"))
        blocks.append(Text(f"Tool: {self.confirmation.tool_name}", style="muted"))
        blocks.append(Text(f"Risk: {self._risk_label}", style=self._risk_style))
        blocks.append(Text(f"Approval scope: {self._session_scope()}", style="muted"))
        if self.command.strip():
            lexer = "powershell" if self.shell_type == ShellType.POWERSHELL else "bat" if self.shell_type == ShellType.CMD else "bash"
            blocks.extend((
                Text("FULL COMMAND", style="muted"),
                Syntax(
                    self.command,
                    lexer,
                    theme=KRYTH_SYNTAX,
                    word_wrap=True,
                    background_color="default",
                    padding=0,
                ),
            ))
        params = dict(self.confirmation.params)
        params.pop("command", None)
        if params:
            blocks.extend((Text("ARGUMENTS", style="muted"), render_args_table(self.confirmation.tool_name, params)))
        if self.confirmation.diff:
            blocks.append(diff_viewer(
                self.confirmation.diff.create_diff(),
                str(self.confirmation.diff.path),
                max_lines=max(8, min(40, width // 2)),
            ))
        blocks.append(Text("Ctrl+O to collapse", style="dim"))
        return tuple(blocks)

    def move(self, delta: int) -> None:
        self.selected = max(0, min(2, self.selected + delta))

    def toggle_details(self) -> None:
        self.expanded = not self.expanded

    @property
    def selected_action(self) -> ApprovalAction:
        return APPROVAL_OPTIONS[self.selected][3]

    def _hints(self) -> Text:
        return Text(
            "Up/Down select  Enter confirm  Ctrl+O command details  Esc cancel",
            style="dim",
        )


def render_approval_choices(selected: int = 0) -> Text:
    """Render the static approval choice hints (non-interactive fallback)."""
    parts: list[tuple[str, str]] = []
    for i, (key, label, style, _action) in enumerate(APPROVAL_OPTIONS):
        if i == selected:
            parts.append((f"bold {style}", f"[{key.upper()}] {label}"))
        else:
            parts.append((style, f"{key.upper()} {label}"))
        if i < len(APPROVAL_OPTIONS) - 1:
            parts.append(("dim", "      "))
    return Text.assemble(*parts)
