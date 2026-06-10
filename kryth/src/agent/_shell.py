"""Shell execution: foreground run_command and background task_output."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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


def run_command(command, timeout=15, run_in_background=False):
    command = COMMAND_ALIASES.get(command, command)

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

        result = subprocess.run(
            runnable,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=bounded_timeout,
        )

        full_output = (result.stdout or "") + (result.stderr or "")
        exit_code = result.returncode

        # Render a unified command panel: header + smart-summarized
        # body + exit-code/severity footer. The renderer composes the
        # whole thing — this tool just hands over the raw output.
        ui.shell_end(
            command=command,
            output=full_output,
            exit_code=exit_code,
            timeout=bounded_timeout,
            note=note,
        )

        # The model still gets the (trimmed) full output as the tool
        # return value, so it can reason about it on the next turn.
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
