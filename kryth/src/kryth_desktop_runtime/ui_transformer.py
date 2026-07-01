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


import re

_TOOL_CALL_RE = re.compile(
    r"</?tool_call>|</?function[^>]*>|</?parameter[^>]*>|"
    r"<function=|<parameter=",
    re.IGNORECASE,
)

# Plain-text tool call patterns: write_file path="..." or write_file(path="...")
_PLAIN_TOOL_CALL_RE = re.compile(
    r"(?:write_file|read_file|run_command|create_file|delete_file|list_files|"
    r"search_code|grep_search|web_search|todo_write|git_commit|git_diff|"
    r"npm_install|pip_install|docker_run|http_request|subagent)"
    r"(?:\s*\(|\s+)(?:path|command|content|query|pattern|url|items)\s*=",
    re.IGNORECASE,
)

# Internal reasoning patterns — LLM thinking aloud
_REASONING_PATTERNS = [
    re.compile(r"^(?:Maybe|Perhaps|Let me|I think|Given the|But (?:the|I|that|again|which)|"
               r"Another|Could be|That (?:seems|would|could|is)|Unless|"
               r"Alternatively|However|Thus|Therefore|I'll|I could|I need to|"
               r"I should|I don't|I can|The user|The instruction|Maybe the user)",
               re.MULTILINE),
]

# Accumulated buffer for detecting reasoning blocks
_content_buffer: list[str] = []
_reasoning_score = 0


def _is_tool_call_fragment(piece: str) -> bool:
    """Return True if a streaming chunk is raw tool-call content that should be hidden."""
    stripped = piece.strip()
    if not stripped:
        return False
    # XML-style tool calls
    if _TOOL_CALL_RE.search(stripped):
        return True
    # Plain-text tool calls like: write_file path="..." content="..."
    if _PLAIN_TOOL_CALL_RE.search(stripped):
        return True
    return False


def _looks_like_internal_reasoning(piece: str) -> bool:
    """Return True if a chunk looks like leaked internal chain-of-thought."""
    stripped = piece.strip()
    if not stripped:
        return False
    # If it contains tool call parameter assignments mixed with reasoning
    if _PLAIN_TOOL_CALL_RE.search(stripped):
        return True
    # Check for reasoning-like patterns (only flag if chunk is long enough
    # to be confident it's not user-facing prose)
    if len(stripped) > 200:
        for pat in _REASONING_PATTERNS:
            matches = pat.findall(stripped)
            if len(matches) >= 3:
                return True
    return False


# Track last tool for linking self-rendered events back to their tool_call
_last_tool_id: str = ""
_last_tool_name: str = ""


def transform(raw_kind: str, raw_id: str, raw_ts: float,
              raw_data: dict) -> list[dict]:
    """Transform one raw BUS event into 0–N UI events.

    Returns an empty list for events that should be silenced.
    Returns multiple events only when a single raw event maps to
    multiple UI concerns.
    """
    global _last_tool_id, _last_tool_name
    k = raw_kind

    # ── Streaming content ────────────────────────────────────────────────
    if k == "llm.content.start":
        return [_ui_event("chat_update", raw_id, raw_ts,
                          {"type": "start"})]

    if k == "llm.content.chunk":
        piece = raw_data.get("piece", "")
        # Filter out raw tool-call XML/text that the LLM sometimes emits
        if _is_tool_call_fragment(piece):
            return []
        # Filter out leaked internal reasoning
        if _looks_like_internal_reasoning(piece):
            return []
        return [_ui_event("chat_update", raw_id, raw_ts,
                          {"type": "chunk", "piece": piece})]

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

    if k in ("turn.end", "runtime.complete"):
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

        events = []

        # If previous tool was self-rendered (no tool.result), mark it done now
        if _last_tool_id and _last_tool_name in ("todo_write", "task_output", "exit_plan_mode"):
            events.append({
                "kind": "tool.result",
                "id": _last_tool_id,
                "ts": raw_ts,
                "data": {"call_id": _last_tool_id, "result": ""},
            })

        _last_tool_id = raw_id
        _last_tool_name = name

        events.append({
            "kind": "tool.start",
            "id": raw_id,
            "ts": raw_ts,
            "data": {"name": name, "args": args},
        })
        return events

    if k in ("tool.result", "tool.finish"):
        result = raw_data.get("result", raw_data.get("output", ""))
        runtime = raw_data.get("runtime", raw_data.get("duration", None))
        return [{
            "kind": "tool.result",
            "id": raw_id,
            "ts": raw_ts,
            "data": {"call_id": _last_tool_id or raw_id, "result": str(result) if result else "", "runtime": runtime},
        }]

    if k == "tool.error":
        return [{
            "kind": "tool.error",
            "id": raw_id,
            "ts": raw_ts,
            "data": {"call_id": _last_tool_id or raw_id, "error": str(raw_data.get("error", raw_data.get("message", "")))},
        }]

    if k == "tool.denied":
        return [{
            "kind": "tool.error",
            "id": raw_id,
            "ts": raw_ts,
            "data": {"call_id": _last_tool_id or raw_id, "error": "Permission denied"},
        }]

    # ── Diffs / file patches ─────────────────────────────────────────────
    if k in ("write.preview", "diff"):
        path   = str(raw_data.get("path", raw_data.get("file", "")))
        before = str(raw_data.get("before", raw_data.get("original", "")))
        after  = str(raw_data.get("after",  raw_data.get("modified", raw_data.get("content", ""))))
        if path:
            hunks = []
            if after:
                lines = []
                if before:
                    for line in before.split("\n"):
                        lines.append({"type": "del", "content": line})
                for line in after.split("\n"):
                    lines.append({"type": "add", "content": line})
                hunks = [{"header": "", "lines": lines}]
            additions = len(after.split("\n")) if after else 0
            deletions = len(before.split("\n")) if before else 0

            events = [_ui_event("file_patch_ready", raw_id, raw_ts, {
                "path": path,
                "before": before,
                "after": after,
                "filename": _basename(path),
                "additions": additions,
                "deletions": deletions,
                "hunks": hunks,
            })]
            # Mark the write tool as done
            if _last_tool_id and _last_tool_name in ("write_file", "create_file", "edit_file", "multi_edit"):
                events.append({
                    "kind": "tool.result",
                    "id": _last_tool_id,
                    "ts": raw_ts,
                    "data": {"call_id": _last_tool_id, "result": f"Wrote {_basename(path)} ({additions} lines)"},
                })
            return events
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
        cwd = str(raw_data.get("cwd", "")) if raw_data.get("cwd") else None
        evt = {
            "kind": "shell.run",
            "id": raw_id,
            "ts": raw_ts,
            "data": {"command": cmd},
        }
        if cwd:
            evt["data"]["cwd"] = cwd
        return [evt]

    if k == "shell.output":
        return [{
            "kind": "shell.output",
            "id": raw_id,
            "ts": raw_ts,
            "data": raw_data,
        }]

    if k == "shell.end":
        events = [{
            "kind": "shell.end",
            "id": raw_id,
            "ts": raw_ts,
            "data": raw_data,
        }]
        # Mark the run_command tool as done
        if _last_tool_id and _last_tool_name in ("run_command", "execute", "shell"):
            output = str(raw_data.get("output", ""))
            exit_code = raw_data.get("exit_code", 0)
            events.append({
                "kind": "tool.result",
                "id": _last_tool_id,
                "ts": raw_ts,
                "data": {"call_id": _last_tool_id, "result": output, "exit_code": exit_code},
            })
        return events

    # ── Connection / lifecycle ────────────────────────────────────────────
    if k == "connection.ready":
        return [_ui_event("connection_ready", raw_id, raw_ts, raw_data)]

    # ── Plan events — pass through for frontend rendering ─────────────────
    if k in ("plan", "plan.prose"):
        return [{
            "kind": "plan.created",
            "id": raw_id,
            "ts": raw_ts,
            "data": raw_data,
        }]

    # ── LLM reasoning — pass through with summary extract ───────────────
    if k == "llm.reasoning.start":
        return [{"kind": "llm.reasoning.start", "id": raw_id, "ts": raw_ts, "data": {}}]
    if k == "llm.reasoning.chunk":
        return [{"kind": "llm.reasoning.chunk", "id": raw_id, "ts": raw_ts, "data": raw_data}]
    if k == "llm.reasoning.end":
        return [{"kind": "llm.reasoning.end", "id": raw_id, "ts": raw_ts, "data": raw_data}]

    # ── Token usage — pass through ───────────────────────────────────────
    if k == "llm.usage":
        return [{"kind": "llm.usage", "id": raw_id, "ts": raw_ts, "data": raw_data}]

    # ── Run summary (final response) ─────────────────────────────────────
    if k == "run.summary":
        summary_text = raw_data.get("summary", "")
        # If there's meaningful content that wasn't already streamed, emit as chat
        if summary_text and len(summary_text) > 20:
            return [
                _ui_event("chat_update", raw_id, raw_ts, {"type": "start"}),
                _ui_event("chat_update", raw_id, raw_ts, {"type": "chunk", "piece": summary_text}),
                _ui_event("chat_update", raw_id, raw_ts, {"type": "end"}),
            ]
        # Otherwise just signal completion (no card needed — content already displayed)
        return [_ui_event("status_update", raw_id, raw_ts, {"status": "idle"})]

    # ── Agent events — pass through ──────────────────────────────────────
    if k in ("agent.created", "agent.spawned"):
        return [{"kind": "agent.spawned", "id": raw_id, "ts": raw_ts, "data": raw_data}]
    if k in ("agent.done", "agent.task.done"):
        return [{"kind": "agent.done", "id": raw_id, "ts": raw_ts, "data": raw_data}]

    # ── Todos / self-rendered tools completion ────────────────────────────
    # todo_write is SELF_RENDERED — the agent never emits tool.result for it.
    # Detect when the NEXT tool starts and mark the previous one as done.
    if k == "todos":
        # The CLI renders a todo list — pass the data as a tool.result for the last todo_write
        if _last_tool_id and _last_tool_name == "todo_write":
            import json as _json
            return [{
                "kind": "tool.result",
                "id": _last_tool_id,
                "ts": raw_ts,
                "data": {"call_id": _last_tool_id, "result": _json.dumps(raw_data.get("items", raw_data), default=str)},
            }]
        return []

    # ── Pipeline stage progress ──────────────────────────────────────────
    if k == "stage.progress":
        return [{
            "kind": "stage.progress",
            "id": raw_id,
            "ts": raw_ts,
            "data": {
                "index": raw_data.get("index", 0),
                "name": raw_data.get("name", ""),
                "status": raw_data.get("status", ""),
                "detail": raw_data.get("detail", ""),
            },
        }]

    # ── Silence everything else ───────────────────────────────────────────
    return []
