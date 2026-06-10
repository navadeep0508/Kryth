"""Todo list management for the active session."""

from __future__ import annotations

from agent import ui
from agent.tools._results import err


def todo_write(items):
    from agent.session import get_session

    if not isinstance(items, list):
        return err("BAD_ARGS", "items must be a list")

    cleaned = []
    for i, raw in enumerate(items):
        if isinstance(raw, str):
            cleaned.append({"text": raw, "status": "pending"})
            continue
        if not isinstance(raw, dict):
            return err("BAD_ARGS", f"item {i} must be string or object")
        text = raw.get("text") or raw.get("subject")
        status = raw.get("status", "pending")
        if not text:
            return err("BAD_ARGS", f"item {i} missing 'text'")
        if status not in ("pending", "in_progress", "completed"):
            status = "pending"
        cleaned.append({"text": text, "status": status})

    session = get_session()
    session.todos = cleaned

    ui.todos(cleaned)

    return f"Saved {len(cleaned)} todos"


def todo_read():
    from agent.session import get_session
    session = get_session()
    if not session.todos:
        return "(no todos)"
    lines = []
    for t in session.todos:
        lines.append(f"[{t['status']}] {t['text']}")
    return "\n".join(lines)
