"""File-update event handlers — the bridge from the event bus to the
new diff/update rendering.

The agent emits two file-update events today:

    WRITE_PREVIEW  — a new file was written (full content)
    DIFF           — an existing file was edited (unified-diff text)

Both flow through here. The renderer (``renderer.py``) calls
``on_write_preview`` / ``on_diff`` on each matching event; we parse,
build the right panel from ``panels.py``, and print it.

Everything visual lives in ``diff_renderer.py`` and ``panels.py``;
this module is the wiring.
"""

from __future__ import annotations

from agent.ui.console import LOCK, console
from agent.ui.diff_renderer import parse_unified_diff
from agent.ui.events import Event
from agent.ui.panels import (
    create_panel,
    delete_panel,
    multi_update_panel,
    update_panel,
)


def on_write_preview(e: Event) -> None:
    path = e.data["path"]
    content = e.data["content"]
    panel = create_panel(path, content)
    with LOCK:
        console.print()
        console.print(panel)


def on_diff(e: Event) -> None:
    diff_text: str = e.data["diff"]
    path: str | None = e.data.get("path")
    explicit_title: str | None = e.data.get("title")

    parsed = parse_unified_diff(diff_text)
    if parsed.is_empty:
        # Nothing structural to show; an empty diff is usually a no-op
        # multi_edit that hit no matching ranges. Skip silently — the
        # tool result already reports "nothing changed".
        return

    file_label = path or "(unknown)"
    edit_count = _edit_count_from_title(explicit_title)
    if edit_count is not None:
        panel = multi_update_panel(file_label, parsed, edit_count=edit_count)
    else:
        panel = update_panel(file_label, parsed)

    with LOCK:
        console.print()
        console.print(panel)


def on_delete(e: Event) -> None:
    """Future-proof handler for a hypothetical FILE_DELETE event. Not
    wired up yet — kept here so the rendering surface is complete and
    the wiring is a one-line change when the agent gains that event."""
    panel = delete_panel(e.data["path"])
    with LOCK:
        console.print()
        console.print(panel)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edit_count_from_title(title: str | None) -> int | None:
    """The multi_edit tool sets title=``multi-edit · N edits``. Pull the
    count back out so the panel can show an ``N edits`` badge. Returns
    ``None`` for any other / missing title so the regular panel is used.
    """
    if not title:
        return None
    if "multi-edit" not in title:
        return None
    for token in title.replace("·", " ").split():
        if token.isdigit():
            return int(token)
    return None


__all__ = ["on_write_preview", "on_diff", "on_delete"]
