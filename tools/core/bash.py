import asyncio
import os
import fnmatch
from pathlib import Path
import re
import sys

from pydantic import BaseModel, Field

from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult
from tools.core.shells import (
    select_shell, validate_command_shell, ShellMismatchError,
    parse_directory_change,
)
from tools.core.long_running import detect_long_running
from tools.core.persistent_terminal import get_terminal_manager, TerminalStatus
from tools.core.tool_supervisor import should_prefer_native, recommend_native_tool
from utils.development_supervisor import DevelopmentSupervisor

# Whole programs. Their names also occur inside ordinary text, as in
# `git commit -m "fix reboot"`, so they only count where a command can start.
DANGEROUS_PROGRAMS = frozenset({
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "mkfs",
    "fdisk",
    "parted",
})

# Specific enough that seeing them anywhere is reason enough to stop, quoted
# or not: `bash -c "rm -rf /"` is not a mention, it is the thing itself.
DANGEROUS_FRAGMENTS = frozenset({
    "rm -rf /",
    "rm -rf ~",
    "rm -rf /*",
    "dd if=/dev/zero",
    "dd if=/dev/random",
    ":(){ :|:& };:",
    "chmod 777 /",
    "chmod -R 777",
    "init 0",
    "init 6",
})

DANGEROUS_COMMANDS = DANGEROUS_PROGRAMS | DANGEROUS_FRAGMENTS

# Start of the string, or right after an operator that ends the previous
# command, with an optional sudo in front so `sudo reboot` still counts.
_COMMAND_POSITION = r'(?:^|[;&|]|\n)\s*(?:sudo\s+)?'

def find_blocked_entry(command: str) -> str | None:
    """Return the dangerous entry this command matches, or None."""

    lowered = command.lower().strip()

    # Compared lowercased on both sides: `chmod -R 777` carries an uppercase R,
    # so matching it against an already-lowercased command never fired.
    for fragment in sorted(DANGEROUS_FRAGMENTS):
        if fragment.lower() in lowered:
            return fragment

    for program in sorted(DANGEROUS_PROGRAMS):
        if re.search(
            _COMMAND_POSITION + re.escape(program) + r'(?![\w-])',
            lowered
        ):
            return program

    return None

class BashParams(BaseModel):

    command: str = Field(
        ...,
        description='The bash command used to execute system commands',
    )

    timeout: int = Field(
        120,
        ge=1,
        le=600,
        description='Timeout in seconds (defaulted: 120s)'
    )

    cwd: str | None = Field(
        None,
        description='The working directory for the command to be ran'
    )

class BashTool(Tool):

    name = 'bash'
    kind = ToolKind.BASH
    description = "Execute a bash command. Use this for running system wide commands, scripts and usage of CLI Tools."

    schema = BashParams

    def __init__(self, config):
        super().__init__(config)
        self._session_cwd: Path | None = None
        self.development_supervisor = DevelopmentSupervisor(config.cwd)

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation:
        from ui.components.terminal_approval import classify_risk, detect_shell

        params = BashParams(**invocation.params)

        risk_level = classify_risk(params.command)
        shell_type = detect_shell(params.command, invocation.cwd)

        if find_blocked_entry(params.command):
            return ToolConfirmation(
                tool_name=self.name,
                params=invocation.params,
                description=f"Execute (DANGEROUS): {params.command}",
                command=params.command,
                is_dangerous=True,
                risk_level=risk_level.value,
                shell_type=shell_type.value,
            )

        return ToolConfirmation(
            tool_name=self.name,
            params=invocation.params,
            description=f"Execute: {params.command}",
            command=params.command,
            is_dangerous=False,
            risk_level=risk_level.value,
            shell_type=shell_type.value,
        )

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        params = BashParams(**invocation.params)

        if find_blocked_entry(params.command):
            return ToolResult.error_result(
                f'Command blocked: {params.command}',
                metadata = {
                    'blocked': True
                }
            )
        
        if params.cwd:
            cwd = Path(params.cwd)
            if not cwd.is_absolute():
                cwd = invocation.cwd / cwd
        else:
            cwd = self._session_cwd or invocation.cwd
        cwd = cwd.resolve()
        
        if not cwd.exists() or not cwd.is_dir():
            return ToolResult.error_result(
                f"Working directory doesn't exist: {cwd}"
            )
        
        env = self._build_environment()

        # Pin and validate the shell before either short or long-running launch.
        try:
            shell = select_shell(preferred=self.config.terminal_shell, cwd=cwd)
            validate_command_shell(params.command, shell.info.type)
        except (RuntimeError, ValueError) as exc:
            return ToolResult.error_result(
                str(exc), metadata={"error_category": "SHELL_MISMATCH" if isinstance(exc, ShellMismatchError) else "CONFIGURATION_ERROR"}
            )

        directory_target = parse_directory_change(params.command, shell.info.type)
        if directory_target is not None:
            target = Path(directory_target)
            if not target.is_absolute():
                target = cwd / target
            target = target.resolve()
            if not target.is_dir():
                return ToolResult.error_result(
                    f"Working directory doesn't exist: {target}",
                    metadata={"error_category": "WORKDIR_NOT_FOUND", "shell": shell.info.type.value},
                )
            self._session_cwd = target
            return ToolResult.success_result(
                output=f"Working directory: {target}",
                metadata={"shell": shell.info.type.value, "cwd": str(target)},
            )

        self._session_cwd = cwd

        # Detect long-running commands (dev servers, etc.) and run them
        # as persistent background sessions instead of blocking.
        long_running = detect_long_running(params.command, cwd)
        if long_running.is_long_running:
            return await self._execute_long_running(
                invocation, params, cwd, env, long_running
            )

        # Select the appropriate shell adapter. Never label CMD execution as Bash.
        # Shell choice is session/environment configuration. Inferring it from
        # command text silently changes syntax and quoting rules between calls.
        result = await shell.execute(
            command=params.command,
            cwd=cwd,
            env=env,
            timeout=params.timeout,
            progress_callback=invocation.report_progress,
        )
        self.development_supervisor.record_command(
            params.command,
            cwd,
            result.exit_code,
            result.error or ("Command timed out" if result.timed_out else (
                f"Command exited with code {result.exit_code}" if not result.success else None
            )),
        )

        # Attach a native-tool recommendation when the command could be replaced
        # by a safer native tool. This does not block execution.
        native_rec = recommend_native_tool(params.command)
        native_metadata = {}
        if native_rec is not None:
            native_metadata = {
                "native_tool_recommended": native_rec.tool_name,
                "native_tool_reason": native_rec.reason,
                "native_tool_confidence": native_rec.confidence,
                "native_tool_params": native_rec.params_hint,
            }

        if result.timed_out:
            return ToolResult.error_result(
                f'Command timed out after: {params.timeout}',
                metadata={
                    "timed_out": True, "shell": shell.info.type.value,
                    "duration_seconds": result.duration_seconds,
                    "stdout": result.stdout, "stderr": result.stderr,
                    "error_category": "TIMEOUT", **native_metadata,
                },
                exit_code=result.exit_code,
            )

        return ToolResult(
            success=result.success,
            output=result.output,
            error=result.error,
            exit_code=result.exit_code,
            metadata={
                "shell": shell.info.type.value,
                "shell_family": shell.info.family.value,
                "shell_executable": shell.info.executable,
                "duration_seconds": result.duration_seconds,
                "stdout": result.stdout,
                "stderr": result.stderr,
                **native_metadata,
            },
        )

    async def _execute_long_running(
        self,
        invocation: ToolInvocation,
        params: BashParams,
        cwd: Path,
        env: dict[str, str],
        long_running: "LongRunningInfo",
    ) -> ToolResult:
        """Start a long-running command as a persistent terminal session."""
        manager = get_terminal_manager()
        name = long_running.session_name or f"terminal-{len(manager.list_sessions()) + 1}"

        # Reuse an existing session with the same name if it's not running.
        existing = manager.get(name)
        if existing and existing.status in (
            TerminalStatus.RUNNING,
            TerminalStatus.STARTING,
        ):
            info = existing.get_status_info()
            return ToolResult.success_result(
                output=(
                    f"Terminal session '{name}' is already running "
                    f"(status: {info['status']}, pid: {info['pid']}).\n"
                    f"Command: {params.command}\n"
                    f"Ports: {', '.join(str(p) for p in info['ports']) if info['ports'] else 'none'}\n"
                    f"Use terminal_status, terminal_logs, terminal_stop to manage it."
                ),
                metadata=info,
            )

        # Stop and remove any non-running existing session to free the name.
        if existing:
            manager.remove(name)

        terminal = manager.create(
            name=name,
            command=params.command,
            cwd=cwd,
            shell_type=self.config.terminal_shell or "bash",
            auto_restart=False,
            startup_timeout=30.0,
        )

        success = await terminal.start(env=env, wait_for_startup=True)
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
            "\nThe command is running in the background. Manage with: "
            "terminal_status, terminal_logs, terminal_input, terminal_stop, "
            "terminal_restart, terminal_list"
        )

        return ToolResult.success_result(
            output="\n".join(lines),
            metadata={
                **info,
                "long_running": True,
                "command_type": long_running.command_type,
            },
        )

    def _build_environment(self) -> dict[str, str]:
        env = os.environ.copy()

        bash_environment = self.config.bash_environment

        if not bash_environment.ignore_default_excludes:
            for pattern in bash_environment.exclude_patterns:
                keys_to_remove = [k for k in env.keys() if fnmatch.fnmatch(k.upper(), pattern.upper())]

                for k in keys_to_remove:
                    del env[k]

        if bash_environment.set_vars:
            env.update(bash_environment.set_vars)

        return env
