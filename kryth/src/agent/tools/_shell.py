"""Shell execution: foreground run_command and background task_output."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

from agent import ui
from agent.tools._common import (
    RUN_COMMAND_ERROR_MARKER,
    trim_head_tail,
)
from agent.tools._results import err


COMMAND_ALIASES = {
    "test": "pytest",
    "start": "npm start",
    "dev": "npm run dev",
    "install": "pip install -r requirements.txt",
    "run": "python main.py",
}


IS_WINDOWS = sys.platform.startswith("win")


# Lazily resolved path to bash.exe (Git Bash) on Windows. None when not
# found. When present, we can pass bash idioms through it rather than
# trying to translate them piecemeal.
_BASH_PATH: str | None | bool = False  # False = unresolved sentinel


def _resolve_bash() -> str | None:
    global _BASH_PATH
    if _BASH_PATH is not False:
        return _BASH_PATH  # type: ignore[return-value]
    if not IS_WINDOWS:
        _BASH_PATH = None
        return None
    # Prefer Git Bash; check PATH first, then the standard install dirs.
    found = shutil.which("bash") or shutil.which("bash.exe")
    if not found:
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if os.path.isfile(candidate):
                found = candidate
                break
    _BASH_PATH = found
    return found


# Bash idioms cmd.exe can't run natively. When run_command sees one of
# these on Windows AND bash is available, route the whole command
# through bash -c. Cheap, robust, no per-command translation table.
_BASH_IDIOMS = (
    "mkdir -p", "rm -rf", "rm -r", "cp -r", "cp -R", "mv -f",
    "ls -", "cat ", "touch ", "which ", "chmod ", "chown ", "grep ",
    "find ", "head ", "tail ", "echo $", "export ", " && ", " || ",
    " | ", " > ", " >> ", " < ", " 2>", "$(", "`",
)


def _looks_like_bash(cmd: str) -> bool:
    return any(idiom in cmd for idiom in _BASH_IDIOMS)


def _prepare_command(command: str) -> tuple[str, str | None]:
    """Return ``(command_to_run, note)``.

    On Windows, when the command uses bash idioms and bash is on PATH,
    re-route via ``bash -c "..."`` so things like ``mkdir -p`` work.
    ``note`` is a one-line annotation for the log (or ``None``).
    """
    if not IS_WINDOWS:
        return command, None
    if not _looks_like_bash(command):
        return command, None
    bash = _resolve_bash()
    if not bash:
        return command, (
            "warning: command uses bash idioms but bash.exe not found; "
            "install Git for Windows or rewrite for cmd.exe"
        )
    escaped = command.replace('"', '\\"')
    return f'"{bash}" -c "{escaped}"', f"routed via {os.path.basename(bash)}"


# task_id -> {"proc": Popen, "out_path": str, "out_file": file, "command": str}
BACKGROUND_TASKS: dict = {}


def _spawn_background(command):
    task_id = uuid.uuid4().hex[:8]
    out_path = os.path.join(
        tempfile.gettempdir(),
        f"kryth-bg-{task_id}.log",
    )
    out_file = open(out_path, "w", encoding="utf-8", errors="replace")
    runnable, _ = _prepare_command(command)
    proc = subprocess.Popen(
        runnable,
        shell=True,
        stdout=out_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    BACKGROUND_TASKS[task_id] = {
        "proc": proc,
        "out_path": out_path,
        "out_file": out_file,
        "command": command,
    }
    return task_id, out_path


def _run_with_live_box(
    runnable: str,
    command: str,
    timeout: int,
    note: str | None,
) -> tuple[str, int]:
    """Run a command and stream its output in a live Rich panel.

    Shows a live scrolling box while the command runs. Ctrl+C sends
    SIGINT to the process (graceful stop) and returns what was captured.
    Returns (full_output, exit_code).
    """
    import threading
    from rich.live import Live
    from rich.panel import Panel
    from rich.text import Text
    from rich.console import Group
    from agent.ui.console import console as _console

    lines: list[str] = []
    MAX_DISPLAY = 20           # lines visible in the panel at once
    lock = threading.Lock()
    proc = None

    def _render() -> Panel:
        with lock:
            visible = lines[-MAX_DISPLAY:] if lines else ["(waiting for output…)"]
        body = Text("\n".join(visible), overflow="fold", no_wrap=False)
        title = Text.assemble(
            ("◈", "bold #E8FF3A"),
            (" EXEC  ", "bold white"),
            (command[:60], "#CFCFCF"),
        )
        return Panel(body, title=title, title_align="left",
                     border_style="#333333", padding=(0, 1), expand=False)

    def _reader(stream):
        for raw in iter(stream.readline, ""):
            line = raw.rstrip("\n")
            with lock:
                lines.append(line)

    try:
        proc = subprocess.Popen(
            runnable,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        reader_thread = threading.Thread(target=_reader, args=(proc.stdout,), daemon=True)
        reader_thread.start()

        with Live(_render(), console=_console, refresh_per_second=8,
                  auto_refresh=True, transient=True) as live:
            try:
                deadline = time.monotonic() + timeout
                while proc.poll() is None:
                    if time.monotonic() > deadline:
                        proc.kill()
                        with lock:
                            lines.append(f"[timed out after {timeout}s]")
                        break
                    live.update(_render())
                    time.sleep(0.1)
            except KeyboardInterrupt:
                # Ctrl+C — send SIGINT so the child can clean up
                try:
                    proc.send_signal(__import__("signal").SIGINT)
                except Exception:
                    proc.kill()
                with lock:
                    lines.append("[interrupted by user (Ctrl+C)]")
                live.update(_render())

        reader_thread.join(timeout=2)
        exit_code = proc.returncode if proc.returncode is not None else 130

    except Exception as exc:
        with lock:
            lines.append(f"[error: {exc}]")
        exit_code = 1

    full_output = "\n".join(lines)
    return full_output, exit_code


def run_command(command, timeout=15, run_in_background=False):
    command = COMMAND_ALIASES.get(command, command)

    # Auto-detect long-running commands and run them in background
    if not run_in_background:
        # Patterns for commands that typically run indefinitely (dev servers, watchers, etc.)
        long_running_patterns = [
            r"npm run dev",
            r"npm start",
            r"yarn dev",
            r"yarn start",
            r"vite",
            r"webpack.*--watch",
            r"python -m http\.server",
            r"flask run",
            r"uvicorn",
            r"ng serve",
            r"next dev",
            r"nuxt dev",
            r"parcel",
            r"react-scripts start",
            r"ngrok",
            r"serve",
            r"nodemon",
            r"ts-node-dev",
            r"webpack-dev-server",
            r"vite dev",
        ]
        import re
        for pattern in long_running_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                ui.info(f"Auto-detected long-running command, running in background")
                run_in_background = True
                break

    if run_in_background:
        task_id, out_path = _spawn_background(command)
        return (
            f"Started background task {task_id}\n"
            f"Output file: {out_path}\n"
            f"Use task_output(task_id='{task_id}') to fetch output."
        )

    try:
        bounded_timeout = max(1, min(int(timeout), 600))

        runnable, note = _prepare_command(command)
        ui.shell_run(command=command, timeout=bounded_timeout, note=note)

        # Live streaming box — shows output as it arrives, supports Ctrl+C
        full_output, exit_code = _run_with_live_box(
            runnable, command, bounded_timeout, note
        )

        ui.shell_end(
            command=command,
            output=full_output,
            exit_code=exit_code,
            timeout=bounded_timeout,
            note=note,
        )

        for_model = trim_head_tail(full_output)
        if exit_code != 0:
            return err(
                "NON_ZERO_EXIT",
                f"command exited with code {exit_code}",
                for_model,
            )
        return for_model

    except subprocess.TimeoutExpired:
        ui.shell_end(
            command=command, output="(timed out)", exit_code=124,
            timeout=int(timeout), note="timeout",
        )
        return err("TIMEOUT", f"command exceeded {timeout}s timeout")
    except Exception as e:
        ui.shell_end(
            command=command, output=str(e), exit_code=1,
            timeout=int(timeout), note=type(e).__name__,
        )
        return err("EXEC_FAILED", f"shell execution failed", str(e))


def task_output(task_id, kill=False):
    entry = BACKGROUND_TASKS.get(task_id)
    if not entry:
        return err("NOT_FOUND", f"no background task with id {task_id}")

    proc = entry["proc"]

    if kill and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    rc = proc.poll()
    status = "running" if rc is None else f"exited (code={rc})"

    try:
        with open(entry["out_path"], "r", encoding="utf-8", errors="replace") as f:
            output = f.read()
    except Exception as e:
        output = f"(could not read output file: {e})"

    if len(output) > 4000:
        output = trim_head_tail(output, budget=4000)

    header = (
        f"task {task_id} status: {status}\n"
        f"command: {entry['command']}\n"
        f"---"
    )

    if rc is not None and rc != 0:
        return err(
            "NON_ZERO_EXIT",
            f"background task {task_id} exited with code {rc}",
            f"{header}\n{output}",
        )

    return f"{header}\n{output}"
