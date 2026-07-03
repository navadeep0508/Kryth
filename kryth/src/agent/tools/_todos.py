"""Todo list management — unified execution plan source of truth.

Todos are stored in Scratchpad.state.todos (List[TodoStep]), NOT Session.todos.
Session.todos is kept as a UI mirror only.
"""

from __future__ import annotations

from agent import ui
from agent.tools._results import err


def todo_write(items):
    from agent.session import get_session
    from agent.runtime.scratchpad import TodoStep, scratch as _scratchpad

    if not isinstance(items, list):
        return err("BAD_ARGS", "items must be a list")

    session = get_session()
    scratch = _scratchpad

    cleaned_steps = []
    for i, raw in enumerate(items):
        if isinstance(raw, str):
            cleaned_steps.append(TodoStep(
                id=f"step_{i}",
                title=raw,
                status="pending",
            ))
            continue
        if not isinstance(raw, dict):
            return err("BAD_ARGS", f"item {i} must be string or object")
        text = raw.get("text") or raw.get("subject")
        status = raw.get("status", "pending")
        if not text:
            return err("BAD_ARGS", f"item {i} missing 'text'")
        # Map legacy status values
        if status == "in_progress":
            status = "active"
        if status not in ("pending", "active", "completed", "blocked", "failed"):
            status = "pending"
        step = TodoStep(
            id=f"step_{i}",
            title=text,
            status=status,
            tool_hint=raw.get("tool_hint"),
            verification_required=raw.get("verification_required", False),
        )
        cleaned_steps.append(step)

    # Replace Scratchpad execution plan with LLM-supplied plan
    if scratch.state is not None:
        scratch.state.todos = cleaned_steps
        # Set first non-completed step as active
        _found_active = False
        for step in scratch.state.todos:
            if step.status in ("pending", "active"):
                if not _found_active:
                    step.status = "active"
                    _found_active = True
                else:
                    step.status = "pending"
        scratch.state.active_todo_idx = _find_first_active(cleaned_steps)
        scratch._recompute_state()

    # Mirror to Session.todos for backward-compatible UI rendering
    session.todos = [{"text": t.title, "status": t.status} for t in cleaned_steps]

    ui.todos(session.todos)

    return f"Saved {len(cleaned_steps)} todos"


def todo_read():
    from agent.runtime.scratchpad import scratch as _scratchpad
    scratch = _scratchpad
    if scratch.state is None or not scratch.state.todos:
        # Fallback to session.todos for backward compat
        from agent.session import get_session
        session = get_session()
        if not session.todos:
            return "(no todos)"
        lines = []
        for t in session.todos:
            lines.append(f"[{t['status']}] {t['text']}")
        return "\n".join(lines)

    lines = []
    for t in scratch.state.todos:
        lines.append(f"[{t.status}] {t.title}")
    return "\n".join(lines)


# ── Internal helpers ─────────────────────────────────────────────────────

def _find_first_active(steps: list) -> int:
    """Return index of first active/pending step, or 0 if none."""
    for i, step in enumerate(steps):
        if step.status in ("active", "pending"):
            return i
    return 0
