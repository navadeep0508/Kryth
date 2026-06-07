"""Phase 1 — Persistent Shell Manager.

Maintains a long-lived shell process so every run_command call does NOT
spawn a new shell. State (cwd, env, venv, history) is preserved across
calls. Falls back to subprocess for Windows when ptyprocess is unavailable.

Supports: bash, zsh, powershell, cmd
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

IS_WINDOWS = sys.platform.startswith("win")

# PTY sentinel — end-of-output marker injected after each command so we
# know when the shell has finished and what the exit code was.
_SENTINEL_PREFIX = "__KRYTH_EXIT__"


def _sentinel(tag: str) -> str:
    return f"{_SENTINEL_PREFIX}_{tag}_"


def _find_shell() -> tuple[str, str]:
    """Return (shell_path, shell_type)."""
    if IS_WINDOWS:
        ps = shutil.which("pwsh") or shutil.which("powershell")
        if ps:
            return ps, "powershell"
        cmd = shutil.which("cmd") or "cmd.exe"
        return cmd, "cmd"
    for name in ("bash", "zsh", "sh"):
        path = shutil.which(name)
        if path:
            return path, name
    return "/bin/sh", "sh"


@dataclass
class ShellEvent:
    kind: str          # "output" | "exit_code" | "error" | "timeout"
    data: Any = None


class PersistentShell:
    """A long-lived shell process with persistent cwd/env/venv state.

    Uses subprocess with threads on Windows; ptyprocess on Unix when available.
    Thread-safe: multiple tools can call execute() without interleaving.
    """

    def __init__(self, shell_path: str | None = None, shell_type: str | None = None):
        if shell_path is None:
            shell_path, shell_type = _find_shell()
        self.shell_path = shell_path
        self.shell_type = shell_type or "bash"
        self._lock = threading.Lock()
        self._proc: Any = None
        self._reader_thread: threading.Thread | None = None
        self._output_queue: queue.Queue[ShellEvent] = queue.Queue()
        self._history: list[dict] = []
        self._cwd: str = os.getcwd()
        self._env: dict = dict(os.environ)
        self._start()

    # ------------------------------------------------------------------
    # Lifecycle

    def _start(self) -> None:
        """Start the shell process."""
        env = dict(self._env)
        # Disable readline editing so we don't get escape codes
        env["TERM"] = "dumb"
        env["PS1"] = ""
        env["PS2"] = ""

        try:
            import ptyprocess  # noqa: F401
            self._start_pty(env)
        except ImportError:
            self._start_subprocess(env)

    def _start_subprocess(self, env: dict) -> None:
        """Fallback: subprocess-based shell (Windows or no ptyprocess)."""
        if self.shell_type in ("powershell", "cmd"):
            # PowerShell: use -Command -; cmd: /Q /K
            if self.shell_type == "powershell":
                args = [self.shell_path, "-NoProfile", "-NonInteractive", "-Command", "-"]
            else:
                args = [self.shell_path, "/Q", "/K", "prompt $G"]
        else:
            args = [self.shell_path, "--norc", "--noprofile", "-i"]

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=self._cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=0,
        )
        self._pty_mode = False
        self._reader_thread = threading.Thread(
            target=self._read_loop_subprocess, daemon=True
        )
        self._reader_thread.start()

    def _start_pty(self, env: dict) -> None:
        """ptyprocess-based shell for Unix — full TTY, interactive-capable."""
        import ptyprocess  # type: ignore

        args: list[str]
        if self.shell_type in ("bash", "sh"):
            args = [self.shell_path, "--norc", "--noprofile"]
        elif self.shell_type == "zsh":
            args = [self.shell_path, "--no-rcs"]
        else:
            args = [self.shell_path]

        self._proc = ptyprocess.PtyProcess.spawn(
            args, env=env, cwd=self._cwd, dimensions=(50, 200)
        )
        self._pty_mode = True
        self._reader_thread = threading.Thread(
            target=self._read_loop_pty, daemon=True
        )
        self._reader_thread.start()

    def _read_loop_subprocess(self) -> None:
        try:
            for line in iter(self._proc.stdout.readline, ""):
                self._output_queue.put(ShellEvent("output", line))
        except Exception as exc:
            self._output_queue.put(ShellEvent("error", str(exc)))

    def _read_loop_pty(self) -> None:
        buf = ""
        try:
            while True:
                try:
                    chunk = self._proc.read(1024)
                except EOFError:
                    break
                except Exception as exc:
                    self._output_queue.put(ShellEvent("error", str(exc)))
                    break
                decoded = chunk.decode("utf-8", errors="replace")
                buf += decoded
                # Split on newlines, keep incomplete trailing line
                lines = buf.split("\n")
                buf = lines[-1]
                for line in lines[:-1]:
                    self._output_queue.put(ShellEvent("output", line))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Command execution

    def _write(self, text: str) -> None:
        if self._pty_mode:
            self._proc.write(text.encode())
        else:
            self._proc.stdin.write(text)
            self._proc.stdin.flush()

    def _build_wrapped_command(self, command: str, tag: str) -> str:
        """Wrap command so we can detect completion and capture exit code."""
        if self.shell_type == "powershell":
            return (
                f"{command}\n"
                f"Write-Host '{_sentinel(tag)}' $LASTEXITCODE\n"
            )
        elif self.shell_type == "cmd":
            return (
                f"{command}\n"
                f"echo {_sentinel(tag)} %ERRORLEVEL%\n"
            )
        else:
            # bash/zsh/sh
            return (
                f"{command}\n"
                f"echo '{_sentinel(tag)}' $?\n"
            )

    def execute(
        self,
        command: str,
        timeout: float = 30.0,
        *,
        on_output: Any = None,
    ) -> tuple[str, int]:
        """Execute command; return (output, exit_code).

        Args:
            command: Shell command to run.
            timeout: Seconds before we give up waiting.
            on_output: Optional callback(line: str) for streaming output.

        Returns:
            (combined_output, exit_code). exit_code=-1 on timeout.
        """
        with self._lock:
            if self._proc is None or self._is_dead():
                self._start()

            tag = uuid.uuid4().hex[:8]
            sentinel_re = re.compile(
                rf"{re.escape(_sentinel(tag))}\s*(\d+)", re.MULTILINE
            )
            wrapped = self._build_wrapped_command(command, tag)

            # Flush stale output before sending
            while not self._output_queue.empty():
                try:
                    self._output_queue.get_nowait()
                except queue.Empty:
                    break

            self._write(wrapped)

            lines: list[str] = []
            exit_code = -1
            deadline = time.monotonic() + timeout

            while time.monotonic() < deadline:
                try:
                    event = self._output_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if event.kind == "error":
                    break

                line = str(event.data or "")
                m = sentinel_re.search(line)
                if m:
                    exit_code = int(m.group(1))
                    # Include any output before sentinel on same line
                    pre = line[:m.start()].rstrip()
                    if pre:
                        lines.append(pre)
                        if on_output:
                            on_output(pre)
                    break

                # Strip ANSI escape codes for clean output
                clean = _strip_ansi(line)
                lines.append(clean)
                if on_output:
                    on_output(clean)

            output = "\n".join(lines)

            # Track history
            self._history.append({
                "command": command,
                "exit_code": exit_code,
                "output_len": len(output),
            })

            # Update tracked cwd after each command
            self._sync_cwd()

            return output, exit_code

    def _sync_cwd(self) -> None:
        """Refresh our cwd record by asking the shell."""
        if self.shell_type == "powershell":
            cwd_cmd = "(Get-Location).Path"
        elif self.shell_type == "cmd":
            cwd_cmd = "cd"
        else:
            cwd_cmd = "pwd"

        tag = uuid.uuid4().hex[:8]
        sentinel_re = re.compile(
            rf"{re.escape(_sentinel(tag))}\s*(\d+)", re.MULTILINE
        )
        self._write(
            f"{cwd_cmd}\necho '{_sentinel(tag)}' $?\n"
            if self.shell_type not in ("powershell", "cmd")
            else f"{cwd_cmd}\nWrite-Host '{_sentinel(tag)}' $LASTEXITCODE\n"
        )

        lines: list[str] = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                event = self._output_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            line = str(event.data or "")
            if sentinel_re.search(line):
                break
            clean = _strip_ansi(line).strip()
            if clean:
                lines.append(clean)

        if lines:
            candidate = lines[-1].strip()
            if os.path.isabs(candidate):
                self._cwd = candidate

    def _is_dead(self) -> bool:
        if self._pty_mode:
            return not self._proc.isalive()
        return self._proc.poll() is not None

    def change_dir(self, path: str) -> bool:
        """Change the shell's working directory. Returns True on success."""
        out, rc = self.execute(f"cd {_quote(path)}", timeout=5.0)
        if rc == 0:
            self._cwd = os.path.normpath(
                os.path.join(self._cwd, path)
            ) if not os.path.isabs(path) else path
            return True
        return False

    def set_env(self, key: str, value: str) -> None:
        """Set an environment variable in the persistent shell."""
        if self.shell_type == "powershell":
            self.execute(f'$env:{key}="{value}"', timeout=3.0)
        elif self.shell_type == "cmd":
            self.execute(f"set {key}={value}", timeout=3.0)
        else:
            self.execute(f'export {key}="{value}"', timeout=3.0)
        self._env[key] = value

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    def close(self) -> None:
        """Terminate the shell process."""
        try:
            if self._pty_mode:
                self._proc.terminate(force=True)
            else:
                self._proc.terminate()
        except Exception:
            pass

    def restart(self) -> None:
        """Kill and restart the shell."""
        self.close()
        self._proc = None
        self._start()


def _quote(path: str) -> str:
    if " " in path:
        return f'"{path}"'
    return path


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", text)


# Module-level singleton — one shell per process, lazily created.
_shell_instance: PersistentShell | None = None
_shell_lock = threading.Lock()


def get_shell() -> PersistentShell:
    global _shell_instance
    with _shell_lock:
        if _shell_instance is None or _shell_instance._is_dead():
            _shell_instance = PersistentShell()
        return _shell_instance
