"""Milestone-checkpoint tool.

Lets the agent record "I just finished phase X" markers into the
persisted session transcript. Useful for long-horizon tasks: the
operator can scan checkpoints on ``/resume`` and see structured
milestones instead of scrolling through raw tool calls.
"""

from __future__ import annotations

from agent.tools._results import err


def checkpoint(label, summary: str = "", modified_files=None):
    """Write a milestone marker into the session transcript.

    ``label``           — short title (kebab-case recommended, max 120 chars).
    ``summary``         — one paragraph describing what was just completed
                          and any decisions worth remembering on resume.
    ``modified_files``  — optional list of paths touched by this milestone.
    """
    if not isinstance(label, str) or not label.strip():
        return err("BAD_ARGS", "checkpoint: label must be a non-empty string")

    if modified_files is not None and not isinstance(modified_files, list):
        return err("BAD_ARGS", "checkpoint: modified_files must be a list of strings")

    from agent.persistence import session_store
    store = session_store()
    cp = store.append_checkpoint(
        label=label.strip(),
        summary=str(summary or ""),
        modified_files=[p for p in (modified_files or []) if isinstance(p, str)],
    )
    if cp is None:
        return "(checkpoint not persisted — session store unavailable)"
    return (
        f"checkpoint recorded: {cp['label']!s} at turn {cp['turn_index']}"
        + (f" ({len(cp['modified_files'])} files)" if cp.get("modified_files") else "")
    )


__all__ = ["checkpoint"]
