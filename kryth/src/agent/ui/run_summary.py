"""Turn-end run summary tracker.

Subscribes to the event bus and aggregates the side-effects of a single
agent turn so the renderer can paint a one-shot "what just happened"
panel at the end:

    ╭─ summary · 12.4s ─────────────────────────╮
    │ ● 3 files written  · index.html, style…   │
    │ ● 1 file edited    · script.js            │
    │ ● 2 commands run   · 1 ok, 1 failed       │
    │ ● 4 tool calls     · 0 retries            │
    ╰───────────────────────────────────────────╯

Decoupled from the agent logic — start_turn() resets state, end_turn()
emits the RUN_SUMMARY event, and the renderer paints the panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.ui.events import BUS, Event, EventKind


@dataclass
class _Counts:
    files_written: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    shell_ok: int = 0
    shell_fail: int = 0
    last_commands: list[str] = field(default_factory=list)
    tools_called: int = 0
    retries: int = 0
    errors: int = 0
    denied: int = 0
    coercions: int = 0
    subagent_spawns: int = 0
    plan_made: bool = False
    tool_counts: dict = field(default_factory=dict)  # tool_name → call count


_state = _Counts()
_unsub = None


def _on_event(e: Event) -> None:
    if e.kind == EventKind.WRITE_PREVIEW:
        _state.files_written.append(e.data.get("path", "?"))
        return
    if e.kind == EventKind.DIFF:
        path = e.data.get("path")
        if path:
            _state.files_edited.append(path)
        return
    if e.kind == EventKind.SHELL_END:
        cmd = e.data.get("command", "")
        _state.last_commands.append(cmd[:80])
        if e.data.get("exit_code", 0) == 0:
            _state.shell_ok += 1
        else:
            _state.shell_fail += 1
        return
    if e.kind == EventKind.TOOL_START:
        _state.tools_called += 1
        name = e.data.get("name", "")
        if name:
            _state.tool_counts[name] = _state.tool_counts.get(name, 0) + 1
        return
    if e.kind == EventKind.TOOL_ERROR:
        _state.errors += 1
        return
    if e.kind == EventKind.LLM_RETRY:
        _state.retries += 1
        return
    if e.kind == EventKind.TOOL_DENIED:
        _state.denied += 1
        return
    if e.kind == EventKind.TOOL_HOOK_BLOCKED:
        _state.denied += 1
        return
    if e.kind == EventKind.TOOL_COERCED:
        _state.coercions += 1
        return
    if e.kind == EventKind.SUBAGENT_START:
        _state.subagent_spawns += 1
        return
    if e.kind == EventKind.PLAN:
        _state.plan_made = True
        return


def install() -> None:
    """Subscribe the tracker to the bus. Safe to call multiple times."""
    global _unsub
    if _unsub is not None:
        return
    _unsub = BUS.subscribe(_on_event)


def reset() -> None:
    """Clear the tracker — called at the start of each turn."""
    global _state
    _state = _Counts()


def snapshot() -> dict:
    """Serialize the current state for the RUN_SUMMARY event."""
    return {
        "files_written": list(_state.files_written),
        "files_edited": list(_state.files_edited),
        "files_deleted": list(_state.files_deleted),
        "shell_ok": _state.shell_ok,
        "shell_fail": _state.shell_fail,
        "last_commands": list(_state.last_commands),
        "tools_called": _state.tools_called,
        "retries": _state.retries,
        "errors": _state.errors,
        "denied": _state.denied,
        "coercions": _state.coercions,
        "subagent_spawns": _state.subagent_spawns,
        "plan_made": _state.plan_made,
        "tool_counts": dict(_state.tool_counts),
    }


def is_empty() -> bool:
    """No side-effects worth summarizing — the agent only chatted."""
    return (
        not _state.files_written
        and not _state.files_edited
        and not _state.files_deleted
        and _state.shell_ok == 0
        and _state.shell_fail == 0
        and _state.tools_called == 0
    )
