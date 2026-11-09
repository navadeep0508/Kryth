"""
Terminal management tools for KRYTH persistent terminal sessions.

Provides tools to start, check status, view logs, send input, stop, restart,
and list persistent terminal sessions for long-running processes like dev servers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult
from tools.core.persistent_terminal import (
    TerminalManager,
    TerminalStatus,
    get_terminal_manager,
)
from tools.core.shells import select_shell, validate_command_shell, ShellMismatchError


# ---------------------------------------------------------------------------
# Terminal Start
# ---------------------------------------------------------------------------

class TerminalStartParams(BaseModel):
    command: str = Field(..., description="The command to run in the persistent terminal")
    name: str | None = Field(None, description="Optional name for the terminal session")
    cwd: str | None = Field(None, description="Working directory (defaults to current)")
    auto_restart: bool = Field(False, description="Auto-restart if the process exits")
    max_restarts: int = Field(3, ge=0, le=10, description="Max restart attempts")
    startup_timeout: float = Field(30.0, ge=1, le=300, description="Seconds to wait for startup evidence")


class TerminalStartTool(Tool):
    name = "terminal_start"
    kind = ToolKind.BASH
    description = "Start a persistent terminal session for a long-running command (e.g., dev server)."

    schema = TerminalStartParams

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        from ui.components.terminal_approval import classify_risk, detect_shell

        params = TerminalStartParams(**invocation.params)
        risk_level = classify_risk(params.command)
        shell_type = detect_shell(params.command, invocation.cwd)
        return ToolConfirmation(
            tool_name=self.name,
            params=invocation.params,
            description=f"Start terminal: {params.command}",
            command=params.command,
            is_dangerous=False,
            risk_level=risk_level.value,
            shell_type=shell_type.value,
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TerminalStartParams(**invocation.params)

        cwd = Path(params.cwd) if params.cwd else invocation.cwd
        if not cwd.is_absolute():
            cwd = invocation.cwd / cwd
        if not cwd.exists() or not cwd.is_dir():
            return ToolResult.error_result(f"Working directory doesn't exist: {cwd}")

        try:
            shell = select_shell(preferred=self.config.terminal_shell, cwd=cwd)
            validate_command_shell(params.command, shell.info.type)
        except (RuntimeError, ValueError) as exc:
            return ToolResult.error_result(
                str(exc), metadata={"error_category": "SHELL_MISMATCH" if isinstance(exc, ShellMismatchError) else "CONFIGURATION_ERROR"}
            )

        manager = get_terminal_manager()
        name = params.name or f"terminal-{len(manager.list_sessions()) + 1}"

        if manager.get(name):
            return ToolResult.error_result(
                f"Terminal session '{name}' already exists. Use terminal_stop or terminal_restart."
            )

        terminal = manager.create(
            name=name,
            command=params.command,
            cwd=cwd,
            shell_type=shell.info.type.value,
            auto_restart=params.auto_restart,
            startup_timeout=params.startup_timeout,
        )
        terminal.session.max_restarts = params.max_restarts

        success = await terminal.start()
        if not success:
            return ToolResult.error_result(
                f"Failed to start terminal session '{name}'",
                metadata=terminal.get_status_info(),
            )

        info = terminal.get_status_info()
        lines = [
            f"Started terminal session '{name}' (session_id: {info['session_id']})",
            f"Command: {params.command}",
            f"Status: {info['status']}",
            f"PID: {info['pid']}",
        ]
        if info["ports"]:
            lines.append(f"Ports: {', '.join(str(p) for p in info['ports'])}")
        lines.append(
            "\nManage with: terminal_status, terminal_logs, terminal_input, "
            "terminal_stop, terminal_restart, terminal_list"
        )

        return ToolResult.success_result(
            output="\n".join(lines),
            metadata=info,
        )


# ---------------------------------------------------------------------------
# Terminal Status
# ---------------------------------------------------------------------------

class TerminalStatusParams(BaseModel):
    name: str = Field(..., description="Name of the terminal session")


class TerminalStatusTool(Tool):
    name = "terminal_status"
    kind = ToolKind.BASH
    description = "Check the status of a persistent terminal session."

    schema = TerminalStatusParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TerminalStatusParams(**invocation.params)
        manager = get_terminal_manager()
        terminal = manager.get(params.name)

        if not terminal:
            return ToolResult.error_result(f"Terminal session '{params.name}' not found.")

        info = terminal.get_status_info()
        lines = [
            f"Session: {info['name']} (session_id: {info['session_id']})",
            f"Command: {info['command']}",
            f"Status: {info['status']}",
            f"PID: {info['pid']}",
            f"Alive: {info['is_alive']}",
            f"Ports: {', '.join(str(p) for p in info['ports']) if info['ports'] else 'none'}",
            f"Restarts: {info['restart_count']}",
            f"Output lines: {info['output_lines']}",
        ]
        if info["exit_code"] is not None:
            lines.append(f"Exit code: {info['exit_code']}")
        if info["started_at"] is not None:
            lines.append(f"Started at: {info['started_at']:.1f}s ago")
        if info["stopped_at"] is not None:
            lines.append(f"Stopped at: {info['stopped_at']:.1f}s ago")

        return ToolResult.success_result(output="\n".join(lines), metadata=info)


# ---------------------------------------------------------------------------
# Terminal Logs
# ---------------------------------------------------------------------------

class TerminalLogsParams(BaseModel):
    name: str = Field(..., description="Name of the terminal session")
    lines: int = Field(50, ge=1, le=1000, description="Number of recent output lines to return")


class TerminalLogsTool(Tool):
    name = "terminal_logs"
    kind = ToolKind.BASH
    description = "View recent output from a persistent terminal session."

    schema = TerminalLogsParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TerminalLogsParams(**invocation.params)
        manager = get_terminal_manager()
        terminal = manager.get(params.name)

        if not terminal:
            return ToolResult.error_result(f"Terminal session '{params.name}' not found.")

        output = terminal.output
        recent = output[-params.lines:] if output else []
        text = "".join(recent) if recent else "(no output yet)"

        return ToolResult.success_result(
            output=text,
            metadata={
                "session": params.name,
                "total_lines": len(output),
                "returned_lines": len(recent),
                "status": terminal.status.value,
            },
        )


# ---------------------------------------------------------------------------
# Terminal Input
# ---------------------------------------------------------------------------

class TerminalInputParams(BaseModel):
    name: str = Field(..., description="Name of the terminal session")
    text: str = Field(..., description="Text to send to the terminal (e.g., stdin input)")


class TerminalInputTool(Tool):
    name = "terminal_input"
    kind = ToolKind.BASH
    description = "Send input to a persistent terminal session's running process."

    schema = TerminalInputParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TerminalInputParams(**invocation.params)
        manager = get_terminal_manager()
        terminal = manager.get(params.name)

        if not terminal:
            return ToolResult.error_result(f"Terminal session '{params.name}' not found.")

        if terminal.status != TerminalStatus.RUNNING:
            return ToolResult.error_result(
                f"Terminal session '{params.name}' is not running (status: {terminal.status.value})."
            )

        process = terminal.session.process
        if process is None or process.stdin is None:
            return ToolResult.error_result(
                f"Terminal session '{params.name}' has no writable stdin."
            )

        try:
            process.stdin.write(params.text.encode())
            await process.stdin.drain()
        except Exception as e:
            return ToolResult.error_result(f"Failed to send input: {e}")

        return ToolResult.success_result(
            output=f"Sent input to session '{params.name}'.",
        )


# ---------------------------------------------------------------------------
# Terminal Stop
# ---------------------------------------------------------------------------

class TerminalStopParams(BaseModel):
    name: str = Field(..., description="Name of the terminal session")
    timeout: float = Field(10.0, ge=1, le=120, description="Seconds to wait before force-killing")


class TerminalStopTool(Tool):
    name = "terminal_stop"
    kind = ToolKind.BASH
    description = "Stop a persistent terminal session and its process tree."

    schema = TerminalStopParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TerminalStopParams(**invocation.params)
        manager = get_terminal_manager()
        terminal = manager.get(params.name)

        if not terminal:
            return ToolResult.error_result(f"Terminal session '{params.name}' not found.")

        success = await terminal.stop(timeout=params.timeout)
        if not success:
            return ToolResult.error_result(
                f"Failed to stop terminal session '{params.name}'.",
                metadata=terminal.get_status_info(),
            )

        info = terminal.get_status_info()
        return ToolResult.success_result(
            output=f"Stopped terminal session '{params.name}'.",
            metadata=info,
        )


# ---------------------------------------------------------------------------
# Terminal Restart
# ---------------------------------------------------------------------------

class TerminalRestartParams(BaseModel):
    name: str = Field(..., description="Name of the terminal session")
    timeout: float = Field(10.0, ge=1, le=120, description="Seconds to wait before force-killing")


class TerminalRestartTool(Tool):
    name = "terminal_restart"
    kind = ToolKind.BASH
    description = "Restart a persistent terminal session (stop then start again)."

    schema = TerminalRestartParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TerminalRestartParams(**invocation.params)
        manager = get_terminal_manager()
        terminal = manager.get(params.name)

        if not terminal:
            return ToolResult.error_result(f"Terminal session '{params.name}' not found.")

        success = await terminal.restart(timeout=params.timeout)
        if not success:
            return ToolResult.error_result(
                f"Failed to restart terminal session '{params.name}' (max restarts reached).",
                metadata=terminal.get_status_info(),
            )

        info = terminal.get_status_info()
        return ToolResult.success_result(
            output=f"Restarted terminal session '{params.name}'.",
            metadata=info,
        )


# ---------------------------------------------------------------------------
# Terminal List
# ---------------------------------------------------------------------------

class TerminalListParams(BaseModel):
    running_only: bool = Field(False, description="Show only running sessions")


class TerminalListTool(Tool):
    name = "terminal_list"
    kind = ToolKind.BASH
    description = "List all persistent terminal sessions."

    schema = TerminalListParams

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = TerminalListParams(**invocation.params)
        manager = get_terminal_manager()
        sessions = manager.list_sessions()

        if params.running_only:
            sessions = [s for s in sessions if s.status == TerminalStatus.RUNNING]

        if not sessions:
            return ToolResult.success_result(
                output="No terminal sessions.",
                metadata={"sessions": [], "count": 0},
            )

        lines = ["Persistent terminal sessions:"]
        session_infos = []
        for s in sessions:
            info = s.get_status_info()
            session_infos.append(info)
            lines.append(
                f"  {info['name']} (id={info['session_id']}) "
                f"status={info['status']} pid={info['pid']} "
                f"ports={','.join(str(p) for p in info['ports']) if info['ports'] else '-'} "
                f"alive={info['is_alive']}"
            )

        return ToolResult.success_result(
            output="\n".join(lines),
            metadata={"sessions": session_infos, "count": len(session_infos)},
        )


__all__ = [
    "TerminalStartTool",
    "TerminalStatusTool",
    "TerminalLogsTool",
    "TerminalInputTool",
    "TerminalStopTool",
    "TerminalRestartTool",
    "TerminalListTool",
]
