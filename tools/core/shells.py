"""
KRYTH Shell Abstraction — cross-platform shell detection and execution.

Provides shell adapters for Windows (CMD, PowerShell, Git Bash, WSL Bash)
and POSIX (Bash, Zsh, Sh) platforms. Each adapter handles command invocation,
argument escaping, environment handling, working directory, exit codes,
output streaming, and process termination for its specific shell.

Never blindly translates commands between shells — each command is executed
in the shell it was written for.
"""

from __future__ import annotations

import abc
import asyncio
import codecs
from functools import lru_cache
import os
import re
import shlex
import signal
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ShellFamily(str, Enum):
    """Broad shell family for capability grouping."""

    WINDOWS_CMD = "cmd"
    WINDOWS_POWERSHELL = "powershell"
    POSIX = "posix"


class ShellType(str, Enum):
    """Concrete shell types detected on the system."""

    CMD = "cmd"
    POWERSHELL = "powershell"
    GIT_BASH = "git_bash"
    WSL_BASH = "wsl_bash"
    LINUX_BASH = "linux_bash"
    MACOS_ZSH = "macos_zsh"
    POSIX_SH = "posix_sh"
    UNKNOWN = "unknown"


@dataclass
class ShellInfo:
    """Describes a detected shell."""

    type: ShellType
    family: ShellFamily
    executable: str | None
    version: str | None = None
    available: bool = False


@dataclass
class ExecutionResult:
    """Result of executing a command in a shell."""

    success: bool
    output: str
    error: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0


class ShellMismatchError(ValueError):
    """Raised when a command contains syntax exclusive to another shell."""


def validate_command_shell(command: str, shell_type: ShellType | str) -> None:
    """Reject unmistakable PowerShell cmdlets when the configured shell differs."""
    selected = shell_type if isinstance(shell_type, ShellType) else ShellType(shell_type)
    if selected == ShellType.POWERSHELL:
        return
    if re.search(r"(?<![\w-])(?:Get|Set|New|Remove|Invoke|Write|Test|ConvertTo|Where|Select|Import|Export)-[A-Za-z]+", command):
        raise ShellMismatchError(
            f"PowerShell cmdlet syntax was provided, but the configured shell is '{selected.value}'. "
            "Select PowerShell explicitly or rewrite the command for the configured shell."
        )


def parse_directory_change(command: str, shell_type: ShellType | str) -> str | None:
    """Return a target for a standalone cd/Set-Location command, if present."""
    selected = shell_type if isinstance(shell_type, ShellType) else ShellType(shell_type)
    match = re.fullmatch(
        r"\s*(?:cd|chdir|Set-Location|sl)\s+(?:(?:/d|--|-[Pp]ath)\s+)?(.+?)\s*",
        command,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
        if selected == ShellType.POWERSHELL and value.startswith("'"):
            value = value.replace("''", "'")
    return value


class ShellAdapter(abc.ABC):
    """Base class for shell adapters.

    Subclasses implement shell-specific invocation, escaping, and teardown.
    """

    def __init__(self, info: ShellInfo) -> None:
        self.info = info

    @abc.abstractmethod
    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        """Build the process argument list for executing *command*."""

    @abc.abstractmethod
    def escape_argument(self, argument: str) -> str:
        """Escape a single argument for this shell's command line."""

    @abc.abstractmethod
    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Return the environment block for this shell."""

    def terminate_process(self, process: asyncio.subprocess.Process) -> None:
        """Best-effort termination of a running process and its process tree."""
        if sys.platform == "win32":
            try:
                import subprocess
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, timeout=5, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    async def execute(
        self,
        command: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 120,
        progress_callback: Any | None = None,
    ) -> ExecutionResult:
        """Execute *command* in this shell and stream output."""
        args = self.build_command(command, cwd)
        shell_env = self.env_vars(env)

        started = time.monotonic()
        process_options: dict[str, Any] = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": cwd,
            "env": shell_env,
        }
        if sys.platform == "win32":
            process_options["stdin"] = asyncio.subprocess.DEVNULL
            process_options["creationflags"] = getattr(
                __import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            process_options["start_new_session"] = True
        try:
            process = await asyncio.create_subprocess_exec(*args, **process_options)
        except OSError:
            # Some embedded Windows hosts expose pipe handles that asyncio
            # cannot duplicate. Keep the same executable/args and use the
            # synchronous subprocess API on a worker thread in that case.
            if sys.platform != "win32":
                raise
            return await asyncio.to_thread(
                self._execute_windows_fallback, args, command, cwd, shell_env,
                timeout, progress_callback, started,
            )

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        pumps = asyncio.gather(
            self._pump(process.stdout, stdout_chunks, progress_callback),
            self._pump(process.stderr, stderr_chunks, None),
        )

        try:
            await asyncio.wait_for(
                asyncio.gather(pumps, process.wait()),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            pumps.cancel()
            self.terminate_process(process)
            await process.wait()
            return ExecutionResult(
                success=False,
                output="".join(stdout_chunks),
                error="".join(stderr_chunks),
                exit_code=process.returncode,
                timed_out=True,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
                duration_seconds=time.monotonic() - started,
            )

        exit_code = process.returncode
        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)

        output = ""
        if stdout.strip():
            output += stdout.rstrip()
        if stderr.strip():
            output += "\n--- stderr ---\n" + stderr.rstrip()
        if exit_code != 0:
            output += f"\nExit code: {exit_code}"

        if len(output) > 100 * 1024:
            output = output[: 100 * 1024] + "\n.. [output truncated]"

        return ExecutionResult(
            success=exit_code == 0,
            output=output,
            error=stderr if exit_code != 0 else None,
            exit_code=exit_code,
            timed_out=False,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
        )

    def _execute_windows_fallback(
        self, args: list[str], command: str, cwd: Path | None,
        shell_env: dict[str, str], timeout: int, progress_callback: Any | None,
        started: float,
    ) -> ExecutionResult:
        import subprocess
        process = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
            env=shell_env, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True, timeout=5, check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError):
                process.kill()
            stdout, stderr = process.communicate()
            timed_out = True
        exit_code = process.returncode
        if progress_callback and stdout:
            try:
                progress_callback(stdout)
            except Exception:
                pass
        return ExecutionResult(
            success=not timed_out and exit_code == 0,
            output=stdout + (("\n--- stderr ---\n" + stderr) if stderr.strip() else ""),
            error=stderr or None,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
        )

    async def _pump(
        self,
        stream: asyncio.StreamReader | None,
        chunks: list[str],
        callback: Any | None,
    ) -> None:
        if stream is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            data = await stream.read(8192)
            if not data:
                break
            text = decoder.decode(data)
            if not text:
                continue
            chunks.append(text)
            if callback:
                try:
                    callback(text)
                except Exception:
                    pass
        tail = decoder.decode(b"", final=True)
        if tail:
            chunks.append(tail)


# ---------------------------------------------------------------------------
# Windows CMD adapter
# ---------------------------------------------------------------------------


class CmdAdapter(ShellAdapter):
    """Windows Command Prompt adapter."""

    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        return [self.info.executable or "cmd.exe", "/d", "/s", "/c", command]

    def escape_argument(self, argument: str) -> str:
        # CMD escaping: wrap in double quotes, escape internal quotes and carets.
        escaped = argument.replace("^", "^^").replace('"', '\\"')
        return f'"{escaped}"'

    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        return env


# ---------------------------------------------------------------------------
# Windows PowerShell adapter
# ---------------------------------------------------------------------------


class PowerShellAdapter(ShellAdapter):
    """Windows PowerShell adapter."""

    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        return [self.info.executable or "powershell.exe", "-NoProfile", "-Command", command]

    def escape_argument(self, argument: str) -> str:
        # PowerShell single-quote strings: escape internal single quotes by doubling.
        escaped = argument.replace("'", "''")
        return f"'{escaped}'"

    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        return env


# ---------------------------------------------------------------------------
# Git Bash adapter (Windows)
# ---------------------------------------------------------------------------


class GitBashAdapter(ShellAdapter):
    """Git Bash on Windows (bash.exe from Git for Windows)."""

    # Common Git Bash installation locations.
    _CANDIDATES = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.path.expandvars(r"%PROGRAMFILES%\Git\bin\bash.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Git\bin\bash.exe"),
    ]

    def __init__(self, info: ShellInfo) -> None:
        super().__init__(info)
        self._bash_exe = self._find_bash()

    def _find_bash(self) -> str | None:
        for candidate in self._CANDIDATES:
            if candidate and os.path.isfile(candidate):
                return candidate
        # Fall back to PATH lookup.
        import shutil

        found = shutil.which("bash.exe")
        if found:
            return found
        return None

    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        bash = self._bash_exe or "bash.exe"
        return [bash, "-c", command]

    def escape_argument(self, argument: str) -> str:
        return shlex.quote(argument)

    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        return env


# ---------------------------------------------------------------------------
# WSL Bash adapter (Windows)
# ---------------------------------------------------------------------------


class WslBashAdapter(ShellAdapter):
    """WSL Bash accessed via wsl.exe from Windows."""

    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        return [self.info.executable or "wsl", "bash", "-c", command]

    def escape_argument(self, argument: str) -> str:
        return shlex.quote(argument)

    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        return env


# ---------------------------------------------------------------------------
# POSIX shell adapters (Linux/macOS)
# ---------------------------------------------------------------------------


class LinuxBashAdapter(ShellAdapter):
    """Linux Bash adapter."""

    def __init__(self, info: ShellInfo) -> None:
        super().__init__(info)
        self._bash = info.executable or "/bin/bash"

    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        return [self._bash, "-c", command]

    def escape_argument(self, argument: str) -> str:
        return shlex.quote(argument)

    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        return env


class MacosZshAdapter(ShellAdapter):
    """macOS Zsh adapter."""

    def __init__(self, info: ShellInfo) -> None:
        super().__init__(info)
        self._zsh = info.executable or "/bin/zsh"

    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        return [self._zsh, "-c", command]

    def escape_argument(self, argument: str) -> str:
        return shlex.quote(argument)

    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        return env


class PosixShAdapter(ShellAdapter):
    """Generic POSIX sh adapter."""

    def __init__(self, info: ShellInfo) -> None:
        super().__init__(info)
        self._sh = info.executable or "/bin/sh"

    def build_command(self, command: str, cwd: Path | None = None) -> list[str]:
        return [self._sh, "-c", command]

    def escape_argument(self, argument: str) -> str:
        return shlex.quote(argument)

    def env_vars(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        if extra:
            env.update(extra)
        return env


# ---------------------------------------------------------------------------
# Shell detection
# ---------------------------------------------------------------------------


def detect_os() -> str:
    """Return the operating system name: 'windows', 'linux', 'darwin'."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unknown"


def _shell_executable_available(executable: str | None) -> bool:
    if not executable:
        return False
    import shutil

    return bool(shutil.which(executable))


@lru_cache(maxsize=16)
def _shell_version(shell_type: ShellType, executable: str | None) -> str | None:
    """Return a short version string without invoking a command shell."""
    if not executable:
        return None
    import subprocess

    probes = {
        ShellType.CMD: [executable, "/d", "/c", "ver"],
        ShellType.POWERSHELL: [executable, "-NoProfile", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        ShellType.GIT_BASH: [executable, "--version"],
        ShellType.LINUX_BASH: [executable, "--version"],
        ShellType.MACOS_ZSH: [executable, "--version"],
        ShellType.POSIX_SH: [executable, "--version"],
        ShellType.WSL_BASH: [executable, "bash", "--version"],
    }
    try:
        result = subprocess.run(
            probes[shell_type], capture_output=True, text=True, timeout=2,
            check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = (result.stdout or result.stderr).replace("\x00", "").strip()
        if shell_type == ShellType.WSL_BASH:
            # `wsl.exe` can exist even when no Linux distribution has been
            # installed yet. In that state it prints setup guidance instead
            # of Bash's version, so do not report WSL Bash as available.
            match = re.search(r"GNU bash, version[^\r\n]*", output)
            return match.group(0)[:160] if match else None
        text = output.splitlines()
        return text[0][:160] if text else None
    except (OSError, subprocess.SubprocessError):
        return None


def _shell_info(shell_type: ShellType, family: ShellFamily, executable: str,
                available: bool = True) -> ShellInfo:
    return ShellInfo(
        type=shell_type, family=family, executable=executable,
        version=_shell_version(shell_type, executable) if available else None,
        available=available,
    )


def detect_available_shells() -> list[ShellInfo]:
    """Detect all shells available on the current system.

    Returns a list of ShellInfo, ordered by preference for the current OS.
    """
    os_name = detect_os()
    shells: list[ShellInfo] = []

    if os_name == "windows":
        # CMD is always available on Windows.
        shells.append(
            ShellInfo(
                type=ShellType.CMD,
                family=ShellFamily.WINDOWS_CMD,
                executable="cmd.exe",
                available=True,
            )
        )

        # PowerShell.
        if _shell_executable_available("powershell.exe"):
            shells.append(
                ShellInfo(
                    type=ShellType.POWERSHELL,
                    family=ShellFamily.WINDOWS_POWERSHELL,
                    executable="powershell.exe",
                    available=True,
                )
            )

        # Git Bash.
        bash_path = None
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            os.path.expandvars(r"%PROGRAMFILES%\Git\bin\bash.exe"),
            os.path.expandvars(r"%PROGRAMFILES(X86)%\Git\bin\bash.exe"),
        ):
            if candidate and os.path.isfile(candidate):
                bash_path = candidate
                break
        if not bash_path:
            import shutil

            found = shutil.which("bash.exe")
            if found:
                bash_path = found
        if bash_path:
            shells.append(
                ShellInfo(
                    type=ShellType.GIT_BASH,
                    family=ShellFamily.POSIX,
                    executable=bash_path,
                    available=True,
                )
            )

        # WSL.
        import shutil

        wsl_executable = shutil.which("wsl")
        wsl_version = _shell_version(ShellType.WSL_BASH, wsl_executable)
        if wsl_version:
            shells.append(
                ShellInfo(
                    type=ShellType.WSL_BASH,
                    family=ShellFamily.POSIX,
                    executable=wsl_executable,
                    version=wsl_version,
                    available=True,
                )
            )

    elif os_name == "darwin":
        shells.append(
            ShellInfo(
                type=ShellType.MACOS_ZSH,
                family=ShellFamily.POSIX,
                executable="/bin/zsh",
                available=_shell_executable_available("/bin/zsh"),
            )
        )
        shells.append(
            ShellInfo(
                type=ShellType.POSIX_SH,
                family=ShellFamily.POSIX,
                executable="/bin/sh",
                available=_shell_executable_available("/bin/sh"),
            )
        )
        if _shell_executable_available("/bin/bash"):
            shells.append(
                ShellInfo(
                    type=ShellType.LINUX_BASH,
                    family=ShellFamily.POSIX,
                    executable="/bin/bash",
                    available=True,
                )
            )

    else:  # linux and unknown
        shells.append(
            ShellInfo(
                type=ShellType.LINUX_BASH,
                family=ShellFamily.POSIX,
                executable="/bin/bash",
                available=_shell_executable_available("/bin/bash"),
            )
        )
        shells.append(
            ShellInfo(
                type=ShellType.POSIX_SH,
                family=ShellFamily.POSIX,
                executable="/bin/sh",
                available=_shell_executable_available("/bin/sh"),
            )
        )
        if _shell_executable_available("/bin/zsh"):
            shells.append(
                ShellInfo(
                    type=ShellType.MACOS_ZSH,
                    family=ShellFamily.POSIX,
                    executable="/bin/zsh",
                    available=True,
                )
            )

    for info in shells:
        if info.available and info.version is None:
            info.version = _shell_version(info.type, info.executable)
    return shells


def detect_configured_shell() -> ShellType | None:
    """Detect the user's configured interactive shell from environment."""
    if sys.platform == "win32":
        explicit = os.environ.get("KRYTH_SHELL", "").strip().lower()
        aliases = {"powershell": ShellType.POWERSHELL, "pwsh": ShellType.POWERSHELL,
                   "cmd": ShellType.CMD, "git-bash": ShellType.GIT_BASH,
                   "git_bash": ShellType.GIT_BASH, "wsl": ShellType.WSL_BASH,
                   "wsl-bash": ShellType.WSL_BASH, "wsl_bash": ShellType.WSL_BASH}
        if explicit:
            return aliases.get(explicit)
        # Check for Git Bash / WSL indicators.
        if os.environ.get("WSL_DISTRO_NAME"):
            return ShellType.WSL_BASH
        if os.environ.get("MSYSTEM") or os.environ.get("GIT_BASH"):
            return ShellType.GIT_BASH
        if os.environ.get("PSModulePath"):
            return ShellType.POWERSHELL
        # Default Windows shell is CMD only when no host-shell marker exists.
        return ShellType.CMD
    shell_env = os.environ.get("SHELL", "")
    if "zsh" in shell_env:
        return ShellType.MACOS_ZSH
    if "bash" in shell_env:
        return ShellType.LINUX_BASH
    if "sh" in shell_env:
        return ShellType.POSIX_SH
    return None


def detect_shell_from_cwd(cwd: Path | str | None) -> ShellType | None:
    """Detect shell type from the working directory path (WSL indicator)."""
    if not cwd:
        return None
    cwd_str = str(cwd)
    if cwd_str.startswith("//wsl$") or "wsl" in cwd_str.lower():
        return ShellType.WSL_BASH
    return None


def select_shell(
    preferred: ShellType | str | None = None,
    cwd: Path | str | None = None,
) -> ShellAdapter:
    """Select the best available shell adapter.

    Priority:
    1. Explicitly requested shell type (if available).
    2. Configured shell from environment (if available).
    3. CWD-based hint (WSL).
    4. OS default (CMD on Windows, Bash/Zsh on POSIX).
    """
    available = {s.type: s for s in detect_available_shells()}

    # 1. Explicit preference.
    if preferred:
        pref = ShellType(preferred) if not isinstance(preferred, ShellType) else preferred
        if pref not in available or not available[pref].available:
            raise RuntimeError(f"Configured shell '{pref.value}' is not available")
        return _adapter_for(available[pref])

    # 2. Configured shell.
    configured = detect_configured_shell()
    if configured and configured in available and available[configured].available:
        return _adapter_for(available[configured])

    # 3. CWD hint.
    cwd_hint = detect_shell_from_cwd(cwd)
    if cwd_hint and cwd_hint in available and available[cwd_hint].available:
        return _adapter_for(available[cwd_hint])

    # 4. OS default.
    os_name = detect_os()
    if os_name == "windows":
        if ShellType.CMD in available:
            return _adapter_for(available[ShellType.CMD])
        # Fall back to first available.
        for info in detect_available_shells():
            if info.available:
                return _adapter_for(info)
    elif os_name == "darwin":
        for st in (ShellType.MACOS_ZSH, ShellType.LINUX_BASH, ShellType.POSIX_SH):
            if st in available and available[st].available:
                return _adapter_for(available[st])
    else:
        for st in (ShellType.LINUX_BASH, ShellType.POSIX_SH):
            if st in available and available[st].available:
                return _adapter_for(available[st])

    # Last resort: raise if nothing available.
    raise RuntimeError("No shell adapter available on this system")


def _adapter_for(info: ShellInfo) -> ShellAdapter:
    """Create the appropriate adapter for a ShellInfo."""
    adapters: dict[ShellType, type[ShellAdapter]] = {
        ShellType.CMD: CmdAdapter,
        ShellType.POWERSHELL: PowerShellAdapter,
        ShellType.GIT_BASH: GitBashAdapter,
        ShellType.WSL_BASH: WslBashAdapter,
        ShellType.LINUX_BASH: LinuxBashAdapter,
        ShellType.MACOS_ZSH: MacosZshAdapter,
        ShellType.POSIX_SH: PosixShAdapter,
    }
    adapter_cls = adapters.get(info.type)
    if adapter_cls is None:
        raise ValueError(f"No adapter for shell type: {info.type}")
    return adapter_cls(info)


# ---------------------------------------------------------------------------
# Command → shell inference (informational, never mutates the command)
# ---------------------------------------------------------------------------


def infer_shell_type(command: str) -> ShellType:
    """Infer the likely shell type from a command string.

    This is informational only — the command is always executed in the
    shell selected by select_shell(), never translated between shells.
    """
    lowered = command.lower().strip()
    words = lowered.split()
    first_word = words[0] if words else ""

    # PowerShell cmdlets: Verb-Noun pattern.
    if re.match(r"^[a-z]+-[a-z]+$", first_word):
        return ShellType.POWERSHELL

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

    # PowerShell aliases.
    ps_aliases = {"gci", "gc", "sc", "ni", "ri", "cp", "mv", "gcm", "gh", "select", "where", "foreach"}
    if first_word in ps_aliases:
        return ShellType.POWERSHELL

    # CMD-specific commands.
    cmd_cmds = {
        "dir", "cls", "copy", "del", "erase", "ren", "type", "vol",
        "tasklist", "taskkill", "ipconfig", "netstat", "sfc",
        "chkdsk", "diskpart", "reg", "net", "sc", "shutdown",
        "ver", "md", "rd", "cd",
    }
    if first_word in cmd_cmds:
        return ShellType.CMD

    return ShellType.LINUX_BASH


__all__ = [
    "ShellFamily",
    "ShellType",
    "ShellInfo",
    "ExecutionResult",
    "ShellAdapter",
    "CmdAdapter",
    "PowerShellAdapter",
    "GitBashAdapter",
    "WslBashAdapter",
    "LinuxBashAdapter",
    "MacosZshAdapter",
    "PosixShAdapter",
    "detect_os",
    "detect_available_shells",
    "detect_configured_shell",
    "detect_shell_from_cwd",
    "select_shell",
    "infer_shell_type",
]
