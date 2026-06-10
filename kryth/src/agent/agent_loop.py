"""Agent loop: orchestration of the tool-use cycle.

This module is the brain: it asks the model what to do, dispatches the
tools the model asks for, captures the results, and repeats. Every byte
of user-facing output is emitted as an event on ``agent.ui`` Ã¢-- this
module imports rich nowhere, prints nothing directly, and could be
swapped to a different UI by replacing the subscriber list.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from agent import ui
from agent.context import build_project_map
from agent.dynamic_builder import run_dynamic_build_with_approval
from agent.ecosystem.router import route
from agent.env import getenv
from agent.hooks import HOOK_BLOCK_PREFIX, run_hooks
from agent.llm import ask_llm_stream, ask_planner, summarize
from agent.permissions import ask_user, check_permission
from agent.project_context import git_status_snapshot, load_context_file
from agent.prompts import SYSTEM_PROMPT
from agent.session import get_session
from agent.skills import auto_select_skills, compose_skills
from agent.task_classifier import classify_task
from agent.task_analyzer import TaskAnalyzer
from agent.execution_strategy import decide_execution_strategy
from agent.tools import (
    READ_ONLY_TOOLS,
    RUN_COMMAND_ERROR_MARKER,
    SELF_RENDERED_TOOLS,
    TOOL_SPECS,
    TOOLS,
)
from agent.tools._results import err, is_error

# Orchestration engine — imported lazily to avoid circular imports at module
# load time; the actual import happens inside run_agent when first needed.
# The module-level name allows tests to patch agent.agent_loop.orchestrate.
try:
    from agent.orchestration import orchestrate, ApprovalMode
except Exception:
    orchestrate = None  # type: ignore[assignment]
    ApprovalMode = None  # type: ignore[assignment]


# Effectively unlimited - set to 100000 by default (can be overridden via env var)
MAX_TOOL_TURNS = int(getenv("KRYTH_MAX_TOOL_TURNS", "100000"))
COMPACT_AT_TOKENS = 30000   # ~90k real tokens — compact less often, fewer LLM round-trips
KEEP_RECENT_AFTER_COMPACT = 6


LoopStatus = Literal["done", "max_turns", "interrupted", "api_error"]


@dataclass
class LoopResult:
    """Structured outcome of ``run_inner_loop``.

    Replaces the older ``__MAX_TURNS__`` / ``__INTERRUPTED__`` sentinel
    strings so callers can distinguish completion from timeout from
    user-cancel from gateway failure without parsing prose.
    """
    status: LoopStatus
    content: str = ""        # final assistant text on "done"; "" otherwise
    turns_used: int = 0       # how many inner iterations the loop ran

    @property
    def incomplete(self) -> bool:
        """True when the agent stopped before finishing the task."""
        return self.status != "done"


# Legacy sentinel constants Ã¢-- retained as module attributes so any third
# party code reading them keeps working, but the loop itself returns
# ``LoopResult`` now.
DONE = "done"
MAX_TURNS = "max_turns"
INTERRUPTED = "interrupted"
API_ERROR = "api_error"


# TypeError messages that almost certainly indicate the model passed
# the wrong arguments (vs. a TypeError raised inside the tool body).
_BAD_ARG_TYPEERR_HINTS = (
    "got an unexpected keyword argument",
    "missing",
    "required positional argument",
    "required keyword-only argument",
    "got multiple values for",
    "takes",
)


def _tool_schema_snippet(tool_name: str) -> str:
    """One-line description + parameter signature for a tool, returned
    as a hint when the model passes bad arguments."""
    spec = _TOOL_SPEC_BY_NAME.get(tool_name)
    if not spec:
        return ""
    fn = spec.get("function", {})
    params = fn.get("parameters", {}) or {}
    props = params.get("properties", {}) or {}
    required = set(params.get("required", []) or [])

    sig_parts = []
    for key, prop in props.items():
        marker = "*" if key in required else ""
        sig_parts.append(f"{key}{marker}: {prop.get('type', 'any')}")
    sig = ", ".join(sig_parts)
    desc = fn.get("description", "").strip().split("\n", 1)[0]
    return f"signature: {tool_name}({sig})\n{desc}" if desc else f"signature: {tool_name}({sig})"


def execute_tool(tool_name, args):
    tool = TOOLS.get(tool_name)
    if not tool:
        return err("TOOL_NOT_FOUND", f"unknown tool: {tool_name}")
    try:
        return tool(**args)
    except TypeError as e:
        msg = str(e)
        if any(hint in msg for hint in _BAD_ARG_TYPEERR_HINTS):
            return err(
                "BAD_ARGS",
                f"argument mismatch calling {tool_name}: {msg}",
                _tool_schema_snippet(tool_name),
            )
        # TypeError raised inside the tool body is a runtime failure,
        # not an arg-schema problem. Surface it as EXEC_FAILED.
        return err(
            "EXEC_FAILED",
            f"{tool_name} raised TypeError during execution",
            msg,
        )
    except Exception as e:
        return err(
            "EXEC_FAILED",
            f"{tool_name} raised {type(e).__name__}",
            str(e),
        )


def has_error(output):
    """Used by the renderer to colour the tool-result tee red. Matches
    both the new ``[ERROR ...]`` convention and any legacy markers."""
    return is_error(output) or RUN_COMMAND_ERROR_MARKER in str(output)


_TOOL_SPEC_BY_NAME = {s["function"]["name"]: s for s in TOOL_SPECS}


def _coerce_tool_args(tool_name: str, args: dict) -> dict:
    """Coerce string args to their declared JSON-schema types.

    Some models serialize ``array`` / ``object`` parameters as
    JSON-encoded strings. Parse them back so the underlying tool
    callable receives the type it expects.
    """
    spec = _TOOL_SPEC_BY_NAME.get(tool_name)
    if not spec:
        return args
    props = spec["function"].get("parameters", {}).get("properties", {}) or {}
    out = dict(args)
    for key, value in args.items():
        if not isinstance(value, str):
            continue
        declared = (props.get(key) or {}).get("type")
        if declared not in ("array", "object"):
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue
        if declared == "array" and isinstance(parsed, list):
            out[key] = parsed
            ui.tool_coerced(tool_name, key, arg_type="array", count=len(parsed))
        elif declared == "object" and isinstance(parsed, dict):
            out[key] = parsed
            ui.tool_coerced(tool_name, key, arg_type="object", count=len(parsed))
    return out


def _append_tool_msg(session, call_id: str, name: str, content: str) -> None:
    session.append({
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    })


# After this many consecutive denials of the SAME (tool, args) call we
# stop asking the user / running hooks and just return immediately with
# a stop-trying-this hint. Catches the common failure mode where a hook
# unconditionally blocks a tool and the model keeps retrying.
DENIAL_HARD_STOP = 5
DENIAL_WARN_AT = 3


def _denial_key(tool_name: str, args: dict) -> tuple[str, str]:
    """Stable key for the consecutive-denial counter. Mirrors
    ``permissions._args_signature`` shape but lives here so we don't
    leak the permission internals."""
    from agent.permissions import _args_signature
    return (tool_name, _args_signature(tool_name, args))


def _bump_denial(session, tool_name: str, args: dict) -> int:
    key = _denial_key(tool_name, args)
    session.denial_counts[key] = session.denial_counts.get(key, 0) + 1
    return session.denial_counts[key]


def _clear_denials(session, tool_name: str) -> None:
    """A successful run clears denials for that tool (any args). Stops
    the counter from creeping up over a whole turn of mixed activity."""
    if not session.denial_counts:
        return
    session.denial_counts = {
        k: v for k, v in session.denial_counts.items() if k[0] != tool_name
    }


def _hard_stop_denial_msg(tool_name: str, count: int) -> str:
    return err(
        "INVALID_STATE",
        f"{tool_name} has been denied/blocked {count} times in a row",
        "Stop calling this tool with the same arguments. Pick a different "
        "approach or report the obstacle to the user.",
    )


def dispatch_tool_call(session, call):
    fn = call.get("function", {})
    tool_name = fn.get("name", "")
    raw_args = fn.get("arguments", "") or "{}"
    call_id = call.get("id") or ""

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as e:
        result = err(
            "BAD_ARGS",
            f"tool arguments were not valid JSON: {e}",
            f"Got: {raw_args[:200]}",
        )
        ui.tool_start(tool_name, {})
        ui.tool_error(f"bad tool args Ã- {e}")
        _append_tool_msg(session, call_id, tool_name, result)
        return

    args = _coerce_tool_args(tool_name, args)

    # ---- Repeat-denial circuit breaker --------------------------
    # If this exact (tool, args) has been denied repeatedly, short-circuit
    # before we ask the user or run hooks again. The model gets a hard
    # signal that it must try something else.
    denial_count = session.denial_counts.get(_denial_key(tool_name, args), 0)
    if denial_count >= DENIAL_HARD_STOP:
        ui.tool_start(tool_name, args)
        ui.tool_error(f"repeat-denial stop Ã- {tool_name} (--{denial_count})")
        _append_tool_msg(
            session, call_id, tool_name,
            _hard_stop_denial_msg(tool_name, denial_count),
        )
        return

    # ---- Plan-mode gate ------------------------------------------
    if session.mode == "plan" and tool_name not in READ_ONLY_TOOLS:
        result = err(
            "INVALID_STATE",
            f"tool '{tool_name}' is not allowed in plan mode",
            "Only read-only tools are permitted. Call exit_plan_mode "
            "once your plan is ready.",
        )
        ui.tool_start(tool_name, args)
        ui.tool_error(f"plan-mode block Ã- {tool_name}")
        _append_tool_msg(session, call_id, tool_name, result)
        return

    # ---- Permission gate ----------------------------------------
    decision = check_permission(tool_name, args)
    if decision == "ask":
        decision = ask_user(tool_name, args)
    if decision == "deny":
        n = _bump_denial(session, tool_name, args)
        ui.tool_start(tool_name, args)
        ui.tool_denied(tool_name)
        message = err("PERMISSION_DENIED", f"user declined {tool_name}")
        if n >= DENIAL_WARN_AT:
            message += (
                f"\nThis exact call has now been denied {n} times. "
                f"Stop retrying with the same arguments Ã¢-- try a "
                f"different tool, different args, or ask the user."
            )
        _append_tool_msg(session, call_id, tool_name, message)
        return

    # ---- PreToolUse hook ----------------------------------------
    pre = run_hooks("PreToolUse", tool_name, args)
    if pre and pre.startswith(HOOK_BLOCK_PREFIX):
        n = _bump_denial(session, tool_name, args)
        ui.tool_start(tool_name, args)
        ui.tool_hook_blocked(pre)
        body = err("HOOK_BLOCKED", f"PreToolUse hook blocked {tool_name}", pre)
        if n >= DENIAL_WARN_AT:
            body += (
                f"\nThis exact call has now been blocked {n} times. "
                f"Stop retrying with the same arguments Ã¢-- adjust the "
                f"approach."
            )
        _append_tool_msg(session, call_id, tool_name, body)
        return

    # ---- Execute -------------------------------------------------
    # Check hard limits (max searches, max pages) before running
    try:
        from agent.context_manager import check_limit, compress_result
        limit_msg = check_limit(tool_name)
        if limit_msg:
            ui.tool_start(tool_name, args)
            ui.warn(limit_msg)
            _append_tool_msg(session, call_id, tool_name, limit_msg)
            return
    except ImportError:
        compress_result = None

    ui.tool_start(tool_name, args)

    # Update Mission Control dashboard with current agent task
    try:
        from agent.ui.mission_control import get_active_mc
        _mc = get_active_mc()
        if _mc is not None:
            _agent_role = getattr(session, "_agent_role", "")
            _agent_id = getattr(session, "_agent_id", _agent_role)
            _path = args.get("path") or args.get("command", "")[:40]
            _task_label = f"{tool_name} {_path}".strip()
            _mc.set_agent(_agent_id or _agent_role, _agent_role, "running", _task_label)
    except Exception:
        pass

    result = execute_tool(tool_name, args)
    session.tool_call_count += 1
    _clear_denials(session, tool_name)

    # Push rich events to floating Textual dashboard
    try:
        import sys as _sys
        _dash = _sys.modules.get("agent.ui.dashboard")
        if _dash is not None and _dash.get_active():
            _agent_role = getattr(session, "_agent_role", "")
            _agent_id = getattr(session, "_agent_id", _agent_role)
            _path = (args.get("path") or args.get("command", ""))[:35]

            # Base tool event
            _dash.push_event("tool_used", tool=tool_name, agent=_agent_role,
                             action=f"{tool_name} {_path}".strip())

            # Rich tool stream with icons
            _icons = {
                "read_file": "📖", "write_file": "✎", "edit_file": "✎",
                "multi_edit": "✎", "run_command": "⚡", "grep": "◈",
                "search_code": "◈", "semantic_search": "◈",
                "browser_click": "🌐", "browser_type": "🌐",
                "run_tests": "🧪", "spawn_agent": "◈",
            }
            _icon = _icons.get(tool_name, "·")
            _dash.push_event("tool_stream", agent=_agent_role, icon=_icon,
                             action=f"{tool_name} {_path}".strip(),
                             detail="")

            # Patch viewer for file writes/edits
            if tool_name == "write_file":
                content = args.get("content", "")
                lines = content.count("\n") + 1 if content else 0
                _dash.push_event("patch", filename=_path,
                                 lines_written=0, lines_total=lines, is_new=True)
            elif tool_name in ("edit_file", "multi_edit"):
                old = args.get("old_string", "")
                new = args.get("new_string", "")
                diff = []
                for ln in (old or "").splitlines()[:4]:
                    diff.append(("-", ln[:38]))
                for ln in (new or "").splitlines()[:4]:
                    diff.append(("+", ln[:38]))
                _dash.push_event("patch", filename=_path, diff=diff, is_new=False)

            # File ownership
            if tool_name in ("write_file", "edit_file", "multi_edit") and _path and _agent_role:
                _dash.push_event("file_locked", path=_path, agent=_agent_role)

            # Agent status update
            if _agent_id:
                _dash.push_event("agent_update", id=_agent_id, role=_agent_role,
                                 status="running", task=f"{tool_name} {_path}".strip())
    except Exception:
        pass

    post = run_hooks("PostToolUse", tool_name, args, result)
    if post:
        result = f"{result}\n[PostToolUse hook]\n{post}"

    # Compress large tool results before storing in context
    # (prevents HTML pages and search results from bloating the token count)
    try:
        if compress_result is not None:
            result_str = compress_result(tool_name, str(result))
        else:
            result_str = str(result)
    except Exception:
        result_str = str(result)

    # Auto-compress browser results every N tool calls
    try:
        from agent.context_manager import compress_messages, COMPRESS_EVERY_N
        if session.tool_call_count % COMPRESS_EVERY_N == 0:
            session.messages, dropped = compress_messages(session.messages)
            if dropped > 0:
                ui.debug(f"(context: compressed {dropped:,} chars of old browser results)")
    except Exception:
        pass

    # Tools that render their own visual representation skip the generic tee
    if tool_name not in SELF_RENDERED_TOOLS:
        ui.tool_result(result_str, error=has_error(result_str))
    _append_tool_msg(session, call_id, tool_name, result_str)


_HIGH_SIGNAL_NEEDLES = (
    "[error ", "traceback", "syntaxerror", "assertionerror",
    "failed", " fail ", "exception:", "panic:", "fatal:",
)

# Cheap path-like token detector for focal-file extraction. Catches the
# typical shapes: ``agent/foo.py``, ``src/bar.ts``, ``a.txt``. Filters
# out punctuation-only matches and bare flag tokens like ``-A``.
_PATH_RE = re.compile(r"[A-Za-z_./\\-]+\.[A-Za-z0-9]{1,6}")


def _focal_files(recent_messages: list, limit: int = 12) -> set[str]:
    """Extract file paths referenced in the recent (kept) tail of the
    transcript. Used by the relevance scorer to decide which older
    messages are still load-bearing."""
    seen: set[str] = set()
    for m in recent_messages:
        text = str(m.get("content") or "")
        for call in m.get("tool_calls") or []:
            text += " " + str((call.get("function") or {}).get("arguments") or "")
        for tok in _PATH_RE.findall(text):
            seen.add(tok)
            if len(seen) >= limit:
                return seen
    return seen


def _relevance_tier(msg: dict, focal_files: set[str]) -> str:
    """Score a single message as ``high`` / ``medium`` / ``low``.

    high:    references an error or a focal file Ã¢-- keep intact.
    medium:  meaningful payload (>50 chars) Ã¢-- light truncation.
    low:     bulky tool output with no clear signal Ã¢-- aggressive stub.
    """
    text = str(msg.get("content") or "")
    low = text.lower()

    if any(needle in low for needle in _HIGH_SIGNAL_NEEDLES):
        return "high"
    if focal_files and any(f in text for f in focal_files):
        return "high"
    if len(text) < 250:
        return "high"  # already concise Ã¢-- no point eliding
    if msg.get("role") == "assistant" and msg.get("tool_calls"):
        return "high"  # tool-call shapes are tiny and structurally important
    if len(text) < 1500:
        return "medium"
    return "low"


def _python_fallback_compact(
    to_compress: list,
    focal_files: set[str] | None = None,
) -> tuple[list, int, int]:
    """Deterministic compactor used when the summarizer model is empty
    or down.

    Relevance-aware: high-tier messages (errors, focal-file refs, short
    payloads, tool-call shapes) pass through untouched. Medium-tier
    messages get a head+tail trim. Low-tier bulky tool output collapses
    to a single-line stub. ``focal_files`` carries the path tokens we
    extracted from the messages we're KEEPING, so anything older that
    references the same files survives intact.

    Returns ``(compacted_messages, dropped_messages, dropped_chars)``
    so the caller can tell the user how much detail was lost.
    """
    focal_files = focal_files or set()
    compacted = []
    dropped_messages = 0
    dropped_chars = 0

    for m in to_compress:
        role = m.get("role")
        tier = _relevance_tier(m, focal_files)

        if role == "tool":
            body = str(m.get("content", "") or "")
            if tier == "high":
                # Keep intact unless it's truly enormous; even then,
                # preserve head + tail to retain error context.
                if len(body) <= 6000:
                    compacted.append(m)
                    continue
                head, tail = body[:2400], body[-2400:]
                dropped_messages += 1
                dropped_chars += len(body) - (len(head) + len(tail) + 20)
                compacted.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "name": m.get("name", ""),
                    "content": f"{head}\nÃ¢--[trimmed]Ã¢--\n{tail}",
                })
                continue
            if tier == "medium":
                head, tail = body[:600], body[-300:]
                dropped_messages += 1
                dropped_chars += max(0, len(body) - (len(head) + len(tail) + 20))
                compacted.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "name": m.get("name", ""),
                    "content": f"{head}\nÃ¢--[trimmed]Ã¢--\n{tail}",
                })
                continue
            # low Ã¢-- keep only the stub.
            stub = f"[elided tool result, {len(body)} chars; ask again if needed]"
            if len(body) > len(stub):
                dropped_messages += 1
                dropped_chars += len(body) - len(stub)
            compacted.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id", ""),
                "name": m.get("name", ""),
                "content": stub,
            })
            continue

        if role == "assistant":
            new = {"role": "assistant"}
            if m.get("tool_calls"):
                new["tool_calls"] = m["tool_calls"]
            elif m.get("content"):
                text = str(m["content"])
                if tier == "high":
                    new["content"] = text
                elif tier == "medium" and len(text) > 800:
                    dropped_messages += 1
                    dropped_chars += len(text) - 800
                    new["content"] = text[:600] + " Ã¢--[trimmed]Ã¢-- " + text[-200:]
                elif tier == "low" and len(text) > 400:
                    dropped_messages += 1
                    dropped_chars += len(text) - 400
                    new["content"] = text[:400] + " Ã¢--"
                else:
                    new["content"] = text
            else:
                new["content"] = ""
            compacted.append(new)
            continue

        compacted.append(m)
    return compacted, dropped_messages, dropped_chars


def maybe_compact(session):
    total = session.total_tokens()
    if total < COMPACT_AT_TOKENS:
        return

    msgs = session.messages
    sys_msgs = [m for m in msgs if m.get("role") == "system"]
    rest = [m for m in msgs if m.get("role") != "system"]

    if len(rest) <= KEEP_RECENT_AFTER_COMPACT + 2:
        return

    to_compress = rest[:-KEEP_RECENT_AFTER_COMPACT]
    keep = rest[-KEEP_RECENT_AFTER_COMPACT:]

    ui.compact_start(count=len(to_compress), tokens=total)

    # Subagents (depth > 0) skip the LLM summarizer — they have a short focused
    # task and the round-trip latency (~5-15s) costs more than it saves.
    # Root agent uses LLM summarizer for better quality context retention.
    is_subagent = getattr(session, "depth", 0) > 0
    summary = "" if is_subagent else summarize(to_compress)

    if summary.strip():
        session.messages = sys_msgs + [{
            "role": "system",
            "content": f"[Earlier conversation summary]\n{summary}",
        }] + keep
        return

    focal = _focal_files(keep)
    compacted, dropped_msgs, dropped_chars = _python_fallback_compact(
        to_compress, focal_files=focal,
    )
    ui.compact_fallback(
        dropped_messages=dropped_msgs,
        dropped_chars=dropped_chars,
    )
    session.messages = sys_msgs + compacted + keep


def _detect_tool_loop(tool_calls, recent_history, max_repeats=12):
    """Detect pathological tool loops (same tool, same args, going nowhere).

    Write/edit tools are explicitly excluded — calling write_file 20 times is
    normal when building a project. Only read-only or search tools looping with
    no progress signal a real loop.
    """
    if not tool_calls or not recent_history:
        return False

    # These tools are safe to repeat many times — never flag them as loops.
    _REPEAT_OK = frozenset({
        "write_file", "edit_file", "multi_edit", "run_command",
        "read_file", "grep", "glob", "search_code",
        "browser_click", "browser_type", "browser_scroll",
        "todo_write", "checkpoint",
    })

    current_tools = [call.get("function", {}).get("name", "") for call in tool_calls]

    for tool_name in current_tools:
        if not tool_name or tool_name in _REPEAT_OK:
            continue

        consecutive_count = 0
        for msg in reversed(recent_history[-10:]):
            if msg.get("role") == "tool" and msg.get("name") == tool_name:
                consecutive_count += 1
            elif msg.get("role") == "assistant":
                break

        if consecutive_count >= max_repeats:
            return True

    return False


def _process_tool_calls(session, tool_calls):
    """Dispatch tool calls with parallel execution where safe.

    Independent read-only calls and non-conflicting writes run
    concurrently. Conflicting writes and exclusive commands serialize
    automatically via the tool scheduler's ownership lock system.
    """
    if len(tool_calls) > 500:
        ui.warn(f"Unexpectedly large tool call batch ({len(tool_calls)}), capping at 500")
        tool_calls = tool_calls[:500]

    # Wrap dispatch_tool_call to absorb Ctrl+C per individual call.
    def _safe_dispatch(sess, call):
        try:
            dispatch_tool_call(sess, call)
        except KeyboardInterrupt:
            ui.tool_cancelled()
            sess.append({
                "role": "tool",
                "tool_call_id": call.get("id") or "",
                "name": call.get("function", {}).get("name", ""),
                "content": err("EXEC_FAILED", "tool cancelled by user (Ctrl+C)"),
            })

    if len(tool_calls) == 1:
        _safe_dispatch(session, tool_calls[0])
        return

    try:
        from agent.tool_scheduler import parallel_dispatch
        parallel_dispatch(session, tool_calls, _safe_dispatch)
    except Exception as _sched_err:
        # Scheduler failure falls back to sequential — never breaks the loop.
        ui.muted(f"  (parallel scheduler unavailable: {_sched_err}; sequential fallback)")
        for call in tool_calls:
            _safe_dispatch(session, call)


def _build_assistant_msg(response):
    """Construct the assistant message from a streaming response.

    Gateways may reject assistant messages that carry BOTH content and
    tool_calls. When the model emits both, prefer the tool_calls.
    """
    msg = {"role": "assistant"}
    if response["tool_calls"]:
        msg["tool_calls"] = response["tool_calls"]
    elif response["content"]:
        msg["content"] = response["content"]
    else:
        msg["content"] = ""
    return msg


def _enforce_message_order(session) -> None:
    """Fix invalid role sequences before sending to the LLM.

    Some providers (mistral-nemotron, etc.) reject requests where:
      - 'user' follows 'tool' directly (must be tool → assistant → user)
      - 'user' follows 'user' (consecutive same-role)

    Strategy: scan non-system messages and insert bridging messages where
    the sequence would be rejected.
    """
    msgs = session.messages
    out = []
    for msg in msgs:
        role = msg.get("role")
        prev_role = out[-1].get("role") if out else None

        if role == "user" and prev_role == "tool":
            # Insert a minimal assistant acknowledgement to bridge the gap.
            out.append({"role": "assistant", "content": ""})
        elif role == "user" and prev_role == "user":
            # Merge consecutive user messages so the sequence stays valid.
            out[-1] = {
                "role": "user",
                "content": (out[-1].get("content") or "") + "\n" + (msg.get("content") or ""),
            }
            continue

        out.append(msg)

    session.messages = out


def run_inner_loop(session, max_turns: int, *, verbose_usage: bool = True) -> LoopResult:
    turn_count = 0
    _consecutive_no_tool_turns = 0
    _total_tool_calls = 0
    # Subagents (depth > 0) are workers with a clear task — allow fewer
    # idle turns before declaring done, cutting wasted LLM round-trips.
    _is_subagent = getattr(session, "depth", 0) > 0
    _start_ts = getenv("_KRYTH_LOOP_TS", "")  # for elapsed-time display

    # Context supervisor replaces maybe_compact — tiered budget-aware compression
    try:
        from agent.context_supervisor import ContextSupervisor
        _supervisor: ContextSupervisor | None = ContextSupervisor(session)
    except Exception:
        _supervisor = None

    for _ in range(max_turns):
        turn_count += 1
        # Run tiered context supervision instead of threshold-only maybe_compact
        if _supervisor is not None:
            _supervisor.check()
        else:
            maybe_compact(session)
        _enforce_message_order(session)

        # Emit live progress for subagents so the UI shows current state.
        if _is_subagent and turn_count > 1:
            role = getattr(session, "_agent_role", "")
            label = f"◈ {role} · turn {turn_count}/{max_turns}" if role else f"◈ turn {turn_count}/{max_turns}"
            ui.llm_waiting(label)

        response = ask_llm_stream(session.messages, tools=TOOL_SPECS)

        usage = response.get("usage") or {}
        if usage:
            session.cumulative_in_tokens += usage.get("prompt_tokens", 0)
            session.cumulative_out_tokens += usage.get("completion_tokens", 0)
            if verbose_usage:
                ui.llm_usage(
                    turn_in=usage.get("prompt_tokens", 0),
                    turn_out=usage.get("completion_tokens", 0),
                    session_in=session.cumulative_in_tokens,
                    session_out=session.cumulative_out_tokens,
                )

        if response.get("interrupted"):
            reason = response.get("finish_reason")
            if reason == "context_overflow":
                # Input too large — force compaction and retry this turn.
                ui.muted("(context overflow — compacting and retrying…)")
                maybe_compact(session)
                turn_count -= 1  # don't count this as a used turn
                continue
            if reason in ("api_error", "stream_error", "timeout"):
                return LoopResult(status="api_error", turns_used=turn_count)
            return LoopResult(status="interrupted", turns_used=turn_count)

        session.append(_build_assistant_msg(response))

        # finish_reason == "length" means the model hit the OUTPUT token cap
        # mid-generation (not a context overflow). Tell the llm layer to cache
        # a higher limit for this model so the next call uses more output tokens,
        # then silently pop the truncated message and retry — no compaction needed.
        if response.get("finish_reason") == "length":
            # Output token cap hit — bump the cached limit and retry silently.
            # Compaction is NOT the fix here (this is an output limit, not input overflow).
            try:
                from agent.llm import _model_max_tokens_cache, MAIN_MODEL
                current = _model_max_tokens_cache.get(MAIN_MODEL, 16384)
                _model_max_tokens_cache[MAIN_MODEL] = min(current * 2, 65536)
            except Exception:
                pass
            if session.messages and session.messages[-1].get("role") == "assistant":
                session.messages.pop()
            turn_count -= 1
            continue

        tool_calls = response["tool_calls"] or []
        if not tool_calls:
            _consecutive_no_tool_turns += 1
            content = response["content"] or ""

            # Subagents: 4 idle turns max (they write summary + may briefly pause).
            # 2 was too aggressive — an agent writing files often outputs a text
            # completion summary then needs 1-2 more turns before actually stopping.
            # Root agent: 6 idle turns (it orchestrates and may write multi-part replies).
            _max_no_tool = 4 if _is_subagent else (6 if _total_tool_calls > 0 else 2)

            if _consecutive_no_tool_turns < _max_no_tool:
                session.append({
                    "role": "user",
                    "content": (
                        "[system] Keep going. You have not finished yet. "
                        "Call tools to complete all remaining tasks. "
                        "Do not stop until every task in the todo list is done."
                    ),
                })
                continue

            return LoopResult(
                status="done",
                content=content,
                turns_used=turn_count,
            )

        # Tools were called — reset the no-tool counter.
        _consecutive_no_tool_turns = 0
        _total_tool_calls += len(tool_calls)

        # Detect infinite tool loops
        if _detect_tool_loop(tool_calls, session.messages):
            ui.warn("Tool loop detected — same tool called repeatedly. Breaking loop.")
            return LoopResult(
                status="interrupted",
                content="",
                turns_used=turn_count,
            )

        hermes_recovered = response.get("finish_reason") in (
            "recovered_after_stream_error", None
        ) and any(
            (tc.get("id") or "").startswith("hermes_") for tc in tool_calls
        )
        _process_tool_calls(session, tool_calls)

        # When tool calls came from Hermes/text-stream recovery (not structured
        # tool_calls from the API), some models don't automatically continue
        # after seeing the tool result. Inject a lightweight nudge so the model
        # knows to proceed with the next step instead of stopping.
        if hermes_recovered:
            session.append({
                "role": "assistant",
                "content": (
                    "Tool call completed. Continuing with the next step."
                ),
            })

    # Max turns reached - warn the user
    ui.warn(f"Reached maximum tool turns ({max_turns}). Task may be incomplete. Consider breaking into smaller steps or increasing KRYTH_MAX_TOOL_TURNS.")
    return LoopResult(status="max_turns", turns_used=turn_count)


# ---------------------------------------------------------------------------
# Planning heuristic
# ---------------------------------------------------------------------------

_PLANNER_TRIGGER_WORDS = {
    "add", "build", "create", "implement", "fix", "debug", "refactor",
    "rewrite", "remove", "delete", "update", "change", "migrate", "split",
    "merge", "rename", "test", "run", "investigate", "find", "make",
    "write", "design", "extract", "wire", "integrate", "optimize",
    "set", "setup", "scaffold", "generate", "deploy",
    "app", "application", "website", "site", "page", "landing",
    "dashboard", "cli", "api", "server", "service", "tool", "project",
    "system", "script", "bot", "game", "ui", "frontend", "backend",
    "fullstack", "endpoint", "module", "library", "package",
}

_BUILD_INTENT_WORDS = {
    "app", "application", "website", "site", "landing", "dashboard",
    "cli", "api", "server", "service", "project", "system", "frontend",
    "backend", "fullstack", "bot", "game",
}


def _should_plan(user_input: str) -> bool:
    text = user_input.strip()
    if not text:
        return False
    if text.startswith("/"):
        return False
    words = text.split()
    lowered = {w.lower().strip(",.!?:;\"'") for w in words}
    if lowered & _BUILD_INTENT_WORDS:
        return True
    if len(words) < 4:
        return False
    return bool(lowered & _PLANNER_TRIGGER_WORDS)


def build_initial_system(session, user_input: str = ""):
    from agent.context import ProjectSnapshot, build_focused_map
    from agent.env import getenv_bool

    # --- Auto-init: build graph on first use if enabled ---
    try:
        from agent.memory import memory
        if not memory.graph.is_built() and getenv_bool("KRYTH_AUTO_INIT", True):
            ui.muted("Building project knowledge graph (first run)...")
            memory.init(auto=True)
            ui.muted("Graph built Ã- file watcher started")
    except Exception:
        pass

    # --- Memory-First context loading ---
    # If graph is built, use graph search for the most relevant files.
    # Falls back to snapshot/focused map when graph isn't available.
    graph_context: str | None = None
    try:
        from agent.memory import memory
        if memory.graph.is_built() and user_input:
            files = memory.graph.search(user_input, top_k=12)
            if files:
                graph_context = memory.graph.context_for(files)
                ui.debug(f"(graph context: {len(files)} relevant files)")
    except Exception:
        pass

    if not graph_context:
        # Fallback: snapshot + focused map
        snapshot = ProjectSnapshot()
        project_map, from_cache = snapshot.get_or_build()
        if from_cache:
            ui.debug("(using cached project snapshot)")
        if user_input and session.messages:
            project_map = build_focused_map(user_input)
    else:
        project_map = graph_context

    project_doc = load_context_file()
    git_state = git_status_snapshot()

    session.project_map = project_map

    parts = [SYSTEM_PROMPT]
    if project_doc:
        parts.append(project_doc)
    if git_state:
        parts.append(git_state)
    parts.append(f"Project files:\n{project_map}")

    session.system_prompt = "\n\n".join(parts)


def _plan_hint_for_model(plan: dict) -> str:
    return json.dumps({
        "goal": plan.get("goal", ""),
        "task_type": plan.get("task_type", ""),
        "required_files": plan.get("required_files", []),
        "execution_steps": plan.get("execution_steps", []),
        "validation_steps": plan.get("validation_steps", []),
        "dependencies": plan.get("dependencies", []),
    }, ensure_ascii=False)


def run_agent(user_input, extra_system: str | None = None):
    session = get_session()
    ui.begin_turn()
    ui.turn_start()

    if not session.messages:
        ui.llm_waiting("◈ Scanning project…")
        build_initial_system(session)
        session.ensure_system()

    # Inject fresh graph context on every turn (auto-updates via file watcher)
    try:
        from agent.memory import memory
        if memory.graph.is_built():
            files = memory.graph.search(user_input, top_k=12)
            if files:
                fresh_context = memory.graph.context_for(files)
                # Remove any old dynamic context
                session.messages[:] = [
                    m for m in session.messages
                    if not (m.get("role") == "system" and m.get("content", "").startswith("[Dynamic graph context]"))
                ]
                inject_msg = {"role": "system", "content": f"[Dynamic graph context]\n{fresh_context}"}
                # Insert after the first system message (index 0) or at front
                if session.messages and session.messages[0].get("role") == "system":
                    session.messages.insert(1, inject_msg)
                else:
                    session.messages.insert(0, inject_msg)
    except Exception:
        pass

    if extra_system:
        session.append({"role": "system", "content": extra_system})
    else:
        # --- Ecosystem skill routing (AI-powered, parallel install) ---
        ecosystem_context: str | None = None
        try:
            from agent.ecosystem.executor import run_skill_workflow
            ui.llm_waiting("◈ Selecting skills…")
            skill_ids = route(user_input, use_llm=True)
            if skill_ids:
                ui.auto_skills(skill_ids)
                ui.llm_waiting(f"◈ Loading {len(skill_ids)} skill(s)…")
                ecosystem_context = run_skill_workflow(
                    skill_ids, user_input, show_progress=True
                )
        except Exception:
            pass

        if ecosystem_context:
            session.append({"role": "system", "content": ecosystem_context})
        else:
            # Local skill auto-selection fallback
            auto = auto_select_skills(
                user_input,
                project_context=getattr(session, "project_map", ""),
            )
            if auto:
                ui.auto_skills(auto)
                session.append({"role": "system", "content": compose_skills(auto)})

    # --- Task classification → route to single / pipeline / parallel ---
    _task_profile = None
    try:
        ui.llm_waiting("◈ Classifying task…")
        _task_profile = classify_task(user_input)
        ui.muted(f"  Task: {_task_profile.complexity} / {_task_profile.category} — {_task_profile.reason}")
    except Exception:
        pass  # classifier unavailable — fall through to safe defaults

    plan_dict: dict | None = None
    plan_prose: str = ""
    ecosystem_context: str | None = locals().get("ecosystem_context")  # may be set in else branch

    _complexity = getattr(_task_profile, "complexity", "medium") if _task_profile else "medium"

    # --- Experience Engine: check what worked before ---
    _experience_pred = None
    if _complexity in ("complex", "medium"):
        try:
            from agent.experience import get_experience
            _exp = get_experience(".")
            _similar = _exp.search(user_input)
            if _similar.matches:
                _experience_pred = _exp.predict(user_input)
                # Show dashboard only for complex tasks to avoid noise
                if _complexity == "complex":
                    _exp.report(user_input, render=True)
                else:
                    ui.debug(
                        f"  (experience: {len(_similar.matches)} similar tasks, "
                        f"predicted success {_experience_pred.success_probability:.0%})"
                    )
        except Exception:
            pass

    if _complexity == "complex":
        # --- Complex: full orchestration pipeline ---
        # Intent → Capabilities → Task DAG → Team → Cost → Approval → Scheduler
        if session.mode != "plan" and orchestrate is not None:
            try:
                ui.muted("  Analyzing task for multi-agent orchestration…")
                orch_result = orchestrate(
                    user_input=user_input,
                    project_root=".",
                    project_context=getattr(session, "project_map", ""),
                    multi_agent_mode=getattr(session, "multi_agent_mode", "ASK"),
                    max_turns_per_agent=500,
                    max_workers=8,
                )
                # Persist updated approval mode (e.g. SESSION_APPROVED / ALWAYS_SINGLE)
                if orch_result.mode_updated is not None and ApprovalMode is not None:
                    session.multi_agent_mode = orch_result.mode_updated.value

                if orch_result.approved and orch_result.output:
                    session.append({"role": "user", "content": user_input})
                    session.append({"role": "assistant", "content": orch_result.output})
                    _result = LoopResult(
                        status="done", content=orch_result.output, turns_used=0
                    )
                    try:
                        from agent.persistence import session_store
                        store = session_store()
                        store.update_meta(
                            cumulative_in_tokens=session.cumulative_in_tokens,
                            cumulative_out_tokens=session.cumulative_out_tokens,
                            mode=session.mode, profile=session.profile,
                        )
                        store.write_meta_marker()
                    except Exception:
                        pass
                    run_hooks("Stop", "", {})
                    ui.publish_turn_summary(status="done", turns_used=0)
                    ui.turn_end(
                        tokens_in=session.cumulative_in_tokens,
                        tokens_out=session.cumulative_out_tokens,
                    )
                    return _result

                if not orch_result.approved:
                    ui.muted(f"  Multi-agent declined — {orch_result.explanation}")
                    # Fall through to single-agent path

            except Exception as _oe:
                import traceback
                ui.warn(f"  Orchestration error (falling back to single-agent): {type(_oe).__name__}: {_oe}")
                ui.muted(traceback.format_exc()[-800:])

        # Fallback: planner + single-agent inner loop
        if _should_plan(user_input):
            try:
                plan_dict, plan_prose = ask_planner(user_input)
                if plan_dict:
                    ui.plan(plan_dict)
                elif plan_prose:
                    ui.plan_prose(plan_prose)
            except Exception:
                pass

    elif _complexity == "medium":
        # --- Medium: planner hint, then single-agent sequential execution ---
        # For web automation, inject a hard directive to use browser_use_task
        _category = getattr(_task_profile, "category", "") if _task_profile else ""
        if _category == "web_automation":
            plan_prose = (
                "This is a multi-step web automation task. "
                "You MUST call browser_use_task() with the complete task description as a single call. "
                "Do NOT use open_url, browser_click, browser_type, or extract_data individually. "
                "browser_use_task() handles the entire browser sequence autonomously."
            )
        elif _should_plan(user_input):
            plan_dict, plan_prose = ask_planner(user_input)
            if plan_dict:
                ui.plan(plan_dict)
            elif plan_prose:
                ui.plan_prose(plan_prose)

    # else: simple Ã¢-- no planner, no parallel, straight to inner loop

    if session.mode == "plan":
        ui.plan_mode_active()

    user_content = user_input
    if plan_dict:
        user_content = f"{user_input}\n\n[plan]\n{_plan_hint_for_model(plan_dict)}"
    elif plan_prose:
        user_content = f"{user_input}\n\n[BROWSER AUTOMATION DIRECTIVE] {plan_prose}"
    session.append({"role": "user", "content": user_content})

    result = run_inner_loop(session, MAX_TOOL_TURNS, verbose_usage=True)

    # --- Experience Engine: record task outcome ---
    try:
        from agent.experience import get_experience
        _exp2 = get_experience(".")
        _exp2.learn(
            "task",
            title=user_input[:80],
            summary=result.content[:200] if result.content else "",
            tags=[_complexity, getattr(_task_profile, "category", "coding")],
            success=(result.status == "done"),
            importance=0.7 if _complexity == "complex" else 0.5,
            extra={"turns": result.turns_used, "status": result.status},
        )
    except Exception:
        pass

    # Persist refreshed cumulative tokens / mode / profile so /resume
    # sees the post-turn state.
    try:
        from agent.persistence import session_store
        store = session_store()
        store.update_meta(
            cumulative_in_tokens=session.cumulative_in_tokens,
            cumulative_out_tokens=session.cumulative_out_tokens,
            mode=session.mode,
            profile=session.profile,
        )
        store.write_meta_marker()
    except Exception:
        pass

    if result.status == "interrupted":
        ui.publish_turn_summary(status=result.status,
                                turns_used=result.turns_used)
        ui.turn_interrupted()
    elif result.status == "api_error":
        # Error already surfaced via ui.llm_error.
        return result
    elif result.status == "max_turns":
        ui.publish_turn_summary(status=result.status,
                                turns_used=result.turns_used)
        ui.turn_max_reached()
    else:
        run_hooks("Stop", "", {})
        ui.publish_turn_summary(status=result.status,
                                turns_used=result.turns_used)
        ui.turn_end(
            tokens_in=session.cumulative_in_tokens,
            tokens_out=session.cumulative_out_tokens,
        )
    return result
