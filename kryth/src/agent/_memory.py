"""Persistent memory tool — records facts the agent should remember
across runs.

Three scopes:

    user      ~/.kryth/MEMORY.md          # follows the user anywhere
    project   <project_root>/AGENTS.md       # team-shared conventions
    local     <cwd>/AGENTS.md                # subdir-specific notes

Each call appends a single bullet under a timestamped section so the
file stays diff-readable. Atomic write (temp + rename) so a crash
mid-write doesn't corrupt the existing memory.

The model should call ``add_memory`` only for things worth keeping
across sessions: user preferences, project conventions, infrastructure
quirks, decisions the user explicitly told the agent to remember. It
should NOT use it as a scratchpad — that's what tool_result history is
for within a single session.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from agent.env import home_dir
from agent.tools._results import err


_SCOPES = ("user", "project", "local")


def _user_memory_dir() -> Path:
    return home_dir()


def _target_for(scope: str, cwd: Path) -> Path:
    if scope == "user":
        return _user_memory_dir() / "MEMORY.md"
    if scope == "project":
        # Lazy import so this module doesn't pull in the project-context
        # tree at registry assembly time.
        from agent.project_context import project_root
        return project_root(cwd) / "AGENTS.md"
    # local
    return cwd / "AGENTS.md"


def _atomic_append(path: Path, body: str) -> None:
    """Append ``body`` (which already ends in newline) to ``path``
    atomically. We read-modify-write through a temp file rather than
    raw appending so a crash mid-write doesn't truncate or interleave."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = ""
    if path.is_file():
        existing = path.read_text(encoding="utf-8", errors="replace")
    new_content = existing.rstrip() + ("\n\n" if existing.strip() else "") + body

    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(new_content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_memory(scope: str = "project", text: str = "") -> str:
    """Append a memory bullet under the given scope.

    ``scope``: ``user`` | ``project`` | ``local``
    ``text``:  the fact / instruction / convention to remember.
    """
    if scope not in _SCOPES:
        return err(
            "BAD_ARGS",
            f"scope must be one of {sorted(_SCOPES)}",
            f"got: {scope!r}",
        )
    if not isinstance(text, str) or not text.strip():
        return err("BAD_ARGS", "text must be a non-empty string")

    cwd = Path(".").resolve()
    target = _target_for(scope, cwd)

    ts = time.strftime("%Y-%m-%d %H:%M")
    # Compact single-line entries get rendered as a bullet; multi-line
    # entries get a fenced block so paragraph structure survives.
    stripped = text.strip()
    if "\n" in stripped:
        body = f"### {ts}\n\n{stripped}\n"
    else:
        body = f"- {ts} — {stripped}\n"

    try:
        _atomic_append(target, body)
    except Exception as e:
        return err(
            "EXEC_FAILED",
            f"could not write memory to {target}",
            str(e),
        )

    return f"Memory saved to {target} (scope: {scope})"


__all__ = ["add_memory"]
