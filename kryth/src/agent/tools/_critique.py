"""Self-critique tool.

Lets the agent pause after a non-trivial edit batch and ask a cheap
reviewer model to grade its own work. The reviewer reads a unified diff
between the most recent snapshot and the current file (or the diff for
a list of files) and returns ``[BUG]`` / ``[RISK]`` / ``[SUSPECT]``
findings — or ``LGTM`` if nothing stands out.

Why this is its own tool rather than a hook on every write:
- Critique costs a model call. Auto-running on every micro-edit would
  burn quota.
- The agent chooses when it has reached a meaningful checkpoint
  (feature done, refactor finished). That's the right granularity.
"""

from __future__ import annotations

import difflib
import os
from pathlib import Path

from agent.tools._results import err


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _diff_against_snapshot(path: str) -> tuple[str, str]:
    """Return ``(diff_text, status)`` for ``path``.

    ``status`` describes what kind of comparison happened: ``ok``,
    ``no-snapshot``, ``unreadable``. The diff is empty whenever the file
    contents match the latest snapshot (no change to review).
    """
    from agent import snapshots  # lazy — avoids circular import at module load time
    backups = snapshots.list_snapshots(path)
    current = _read_text(path)
    if current is None:
        return "", "unreadable"
    if not backups:
        return "", "no-snapshot"

    backup_path = backups[0]["backup_path"]
    try:
        with open(backup_path, "r", encoding="utf-8") as f:
            previous = f.read()
    except (OSError, UnicodeDecodeError):
        return "", "unreadable"

    if previous == current:
        return "", "unchanged"

    diff = "\n".join(difflib.unified_diff(
        previous.splitlines(),
        current.splitlines(),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    ))
    return diff, "ok"


def self_critique(paths, intent: str = ""):
    """Ask the reviewer model to grade recent edits to ``paths``.

    ``paths`` may be a single path or a list. Each file is diffed against
    its most recent snapshot; the combined diff is sent to the critic.

    ``intent`` is a one-line description of the goal the edits were
    meant to achieve — gives the reviewer the success criterion to
    measure against.
    """
    from agent.llm import critique

    if isinstance(paths, str):
        path_list = [paths]
    elif isinstance(paths, list):
        path_list = [p for p in paths if isinstance(p, str) and p.strip()]
    else:
        return err(
            "BAD_ARGS",
            "self_critique: paths must be a string or list of strings",
        )

    if not path_list:
        return err("BAD_ARGS", "self_critique: paths is empty")

    diffs: list[str] = []
    statuses: dict[str, str] = {}
    for p in path_list:
        d, status = _diff_against_snapshot(p)
        statuses[p] = status
        if d:
            diffs.append(d)

    if not diffs:
        nothing = ", ".join(f"{p}={statuses[p]}" for p in path_list)
        return f"(no diff to review — {nothing})"

    combined = "\n".join(diffs)
    findings = critique(combined, intent=intent)
    if not findings:
        return "(critique unavailable — proceeding without review)"

    header = f"critic reviewed {len(diffs)} file(s) vs latest snapshot:"
    return header + "\n" + findings


__all__ = ["self_critique"]
