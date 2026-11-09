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

from rich.syntax import Syntax
from rich.text import Text

from tools.base import ToolConfirmation
from ui.format import guess_language
from ui.theme import KRYTH_SYNTAX
from ui.theme.palette import PALETTE, hex_colour
from utils.paths import display_path_relative_to_cwd


class RiskLevel(str, Enum):
    """Command risk classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


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


# --- Risk classification patterns ---

_HIGH_RISK = [
    r"\brm\b.*-rf", r"\brmdir\b", r"\bdel\b\s", r"\berase\b",
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
    """Rich renderable for terminal command approval requests.

    Shows command details, risk classification, shell type, working directory,
    syntax-highlighted command preview, and an interactive approval menu.
    """

    def __init__(
        self,
        confirmation: ToolConfirmation,
        cwd: Path | str | None = None,
        selected: int = 0,
    ) -> None:
        self.confirmation = confirmation
        self.cwd = Path(cwd) if cwd else None
        self.selected = max(0, min(2, selected))

        self.command = confirmation.command or ""
        self.risk_level = getattr(confirmation, "risk_level", RiskLevel.MEDIUM)
        self.shell_type = getattr(confirmation, "shell_type", ShellType.UNKNOWN)
        self.description = confirmation.description or ""

        self._risk_style, self._risk_label = RISK_STYLES[self.risk_level]

    # -- Rich renderable protocol --

    def __rich_console__(self, console: Any, options: Any) -> Any:
        yield self._header()
        description = self._description()
        if description is not None:
            yield description
        yield self._shell_info()
        command = self._command_preview()
        if command is not None:
            yield command
        yield self._menu()
        yield self._hints()

    # -- Panel sections --

    def _header(self) -> Text:
        header = Text("KRYTH wants to execute", style="bright")
        header.append("  |  ", style="dim")
        header.append(self._risk_label, style=f"bold {self._risk_style}")
        header.append("  |  ", style="dim")
        header.append(SHELL_LABELS[self.shell_type], style="muted")
        return header

    def _description(self) -> Text | None:
        desc = self.description
        if desc.startswith("Execute (DANGEROUS): "):
            desc = desc[len("Execute (DANGEROUS): ") :]
        elif desc.startswith("Execute: "):
            desc = desc[len("Execute: ") :]

        if not desc:
            return None

        text = Text(desc, style="muted")

        smart = describe_command(self.command)
        if smart:
            text.append(f"  - {smart}", style="info")

        return text

    def _shell_info(self) -> Text:
        info = Text()
        info.append("in ", style="muted")
        if self.cwd:
            cwd_display = display_path_relative_to_cwd(str(self.cwd), self.cwd)
            info.append(cwd_display or ".", style="muted")
        else:
            info.append(".", style="muted")
        return info

    def _command_preview(self) -> Syntax | None:
        if not self.command.strip():
            return None

        if self.shell_type == ShellType.POWERSHELL:
            lexer = "powershell"
        elif self.shell_type == ShellType.CMD:
            lexer = "bat"
        else:
            lexer = "bash"

        return Syntax(
            self.command,
            lexer,
            theme=KRYTH_SYNTAX,
            word_wrap=True,
            background_color="default",
            padding=0,
        )

    def _menu(self) -> Text:
        menu = Text()
        for i, (key, label, style, _action) in enumerate(APPROVAL_OPTIONS):
            if i > 0:
                menu.append("  |  ", style="dim")
            is_selected = i == self.selected
            if is_selected:
                menu.append("> ", style="bold accent")
                menu.append(f"{key.upper()} {label}", style=f"bold {style}")
            else:
                menu.append(f"{key.upper()} {label}", style=style)
        return menu

    def _hints(self) -> Text:
        return Text(
            "Up/Down choose | Enter select | Esc cancel",
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
            parts.append(("dim", "  ·  "))
    return Text.assemble(*parts)
