"""UI Transformer — converts raw KRYTH EventBus events into UI-safe events.

Raw agent events (tool.start, llm.chunk, etc.) must NEVER reach the
frontend directly. This module normalises them into a flat, UI-friendly
schema and strips internal detail that would pollute the user experience.

UI event schema:
    {kind, id, ts, data}

UI event kinds:
    chat_update       — streaming content chunk or complete assistant message
    status_update     — agent status change (idle/thinking/running)
    action_update     — human-readable tool action status
    file_patch_ready  — diff ready for approval in the inspector
    approval_request  — explicit user confirmation needed
    terminal_update   — shell output chunk (routed to xterm)
    session_event     — lifecycle: turn start / turn end / interrupted
    connection_ready  — bridge is alive
"""

from __future__ import annotations

import uuid
from typing import Any

# Human-readable labels for tool names
_TOOL_LABELS: dict[str, str] = {
    "write_file":    "Updating {path}…",
    "create_file":   "Creating {path}…",
    "read_file":     "Reading {path}…",
    "delete_file":   "Deleting {path}…",
    "run_command":   "Running {command}…",
    "search_code":   "Searching codebase…",
    "list_files":    "Listing files…",
    "grep_search":   'Searching for "{pattern}"…',
    "web_search":    'Searching web for "{query}"…',
    "subagent":      "Spawning sub-agent…",
    "git_commit":    "Creating git commit…",
    "git_diff":      "Checking git diff…",
    "npm_install":   "Installing npm packages…",
    "pip_install":   "Installing Python packages…",
    "docker_run":    "Running container…",
    "http_request":  "Fetching {url}…",
}


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1] or path


def _first_word(s: str) -> str:
    return s.strip().split()[0] if s.strip() else s


def human_tool_label(name: str, args: dict) -> str:
    template = _TOOL_LABELS.get(name)
    if not template:
        return name.replace("_", " ").title() + "…"
    try:
        ctx: dict[str, str] = {}
        if "path" in args:
            ctx["path"] = _basename(str(args["path"]))
        if "command" in args:
            ctx["command"] = _first_word(str(args["command"]))
        if "pattern" in args:
            ctx["pattern"] = str(args["pattern"])[:40]
        if "query" in args:
            ctx["query"] = str(args["query"])[:40]
        if "url" in args:
            ctx["url"] = str(args["url"])[:50]
        return template.format(**ctx)
    except (KeyError, IndexError):
        return template.split("{")[0] + "…"


def _ui_event(kind: str, orig_id: str, ts: float, data: dict) -> dict:
    return {"kind": kind, "id": orig_id, "ts": ts, "data": data}


def transform(raw_kind: str, raw_id: str, raw_ts: float,
              raw_data: dict) -> list[dict]:
    """Transform one raw BUS event into 0–N UI events.

    Returns an empty list for events that should be silenced.
    Returns multiple events only when a single raw event maps to
    multiple UI concerns.
    """
    k = raw_kind

    # ── Streaming content ────────────────────────────────────────────────
    if k == "llm.content.start":
        return [_ui_event("chat_update", raw_id, raw_ts,
                          {"type": "start"})]

    if k == "llm.content.chunk":
        return [_ui_event("chat_update", raw_id, raw_ts,
                          {"type": "chunk", "piece": raw_data.get("piece", "")})]

    if k == "llm.content.end":
        return [_ui_event("chat_update", raw_id, raw_ts,
                          {"type": "end"})]

    # ── Status ───────────────────────────────────────────────────────────
    if k == "llm.waiting":
        return [_ui_event("status_update", raw_id, raw_ts,
                          {"status": "thinking"})]

    if k in ("turn.start",):
        return [_ui_event("session_event", raw_id, raw_ts,
                          {"event": "turn_start"}),
                _ui_event("status_update", raw_id, raw_ts,
                          {"status": "running"})]

    if k in ("turn.end", "run.summary", "runtime.complete"):
        return [_ui_event("session_event", raw_id, raw_ts,
                          {"event": "turn_end", **raw_data}),
                _ui_event("status_update", raw_id, raw_ts,
                          {"status": "idle"})]

    if k == "turn.interrupted":
        return [_ui_event("session_event", raw_id, raw_ts,
                          {"event": "interrupted"}),
                _ui_event("status_update", raw_id, raw_ts,
                          {"status": "idle"})]

    if k == "agent.idle":
        return [_ui_event("status_update", raw_id, raw_ts, {"status": "idle"})]

    # ── Tool actions ─────────────────────────────────────────────────────
    if k == "tool.start":
        name = str(raw_data.get("name", ""))
        args = raw_data.get("args", {}) or {}
        if not isinstance(args, dict):
            args = {}
        return [_ui_event("action_update", raw_id, raw_ts, {
            "tool_id": raw_id,
            "label": human_tool_label(name, args),
            "status": "running",
            "name": name,
        })]

    if k in ("tool.result", "tool.finish"):
        return [_ui_event("action_update", raw_id, raw_ts, {
            "tool_id": raw_id,
            "status": "done",
        })]

    if k == "tool.error":
        return [_ui_event("action_update", raw_id, raw_ts, {
            "tool_id": raw_id,
            "status": "failed",
            "error": str(raw_data.get("error", "")),
        })]

    if k == "tool.denied":
        return [_ui_event("action_update", raw_id, raw_ts, {
            "tool_id": raw_id,
            "status": "denied",
        })]

    # ── Diffs / file patches ─────────────────────────────────────────────
    if k in ("write.preview", "diff"):
        path   = str(raw_data.get("path", raw_data.get("file", "")))
        before = str(raw_data.get("before", raw_data.get("original", "")))
        after  = str(raw_data.get("after",  raw_data.get("modified", "")))
        if path:
            return [_ui_event("file_patch_ready", raw_id, raw_ts, {
                "path": path,
                "before": before,
                "after": after,
                "filename": _basename(path),
            })]
        return []

    # ── Approvals ────────────────────────────────────────────────────────
    if k in ("approval.request", "approval.batch"):
        return [_ui_event("approval_request", raw_id, raw_ts, {
            "message": str(raw_data.get("message", "Allow this action?")),
            "risk":    str(raw_data.get("risk", "medium")),
            "items":   raw_data.get("items", []),
        })]

    # ── Shell / terminal ─────────────────────────────────────────────────
    if k == "shell.run":
        cmd = str(raw_data.get("command", ""))
        return [_ui_event("action_update", raw_id, raw_ts, {
            "tool_id": raw_id,
            "label": f"Running {_first_word(cmd)}…",
            "status": "running",
        })]

    if k == "shell.end":
        return [_ui_event("action_update", raw_id, raw_ts, {
            "tool_id": raw_id,
            "status": "done",
        })]

    # ── Connection / lifecycle ────────────────────────────────────────────
    if k == "connection.ready":
        return [_ui_event("connection_ready", raw_id, raw_ts, raw_data)]

    # ── Silence everything else ───────────────────────────────────────────
    # llm.reasoning.*, llm.usage, token.budget, dag.*, agent.*, plan.*,
    # compact.*, log, subagent.*, etc. — stripped in Phase 1
    return []
