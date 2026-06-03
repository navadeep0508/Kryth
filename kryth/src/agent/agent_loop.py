"""Agent loop: orchestration of the tool-use cycle.

This module is the brain: it asks the model what to do, dispatches the
tools the model asks for, captures the results, and repeats. Every byte
of user-facing output is emitted as an event on ``agent.ui`` — this
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
from agent.env import getenv
from agent.hooks import HOOK_BLOCK_PREFIX, run_hooks
from agent.llm import ask_llm_stream, ask_planner, summarize
from agent.permissions import ask_user, check_permission
from agent.project_context import git_status_snapshot, load_context_file
from agent.prompts import SYSTEM_PROMPT
from agent.session import get_session
from agent.tools import (
    READ_ONLY_TOOLS,
    RUN_COMMAND_ERROR_MARKER,
    SELF_RENDERED_TOOLS,
    TOOL_SPECS,
    TOOLS,
)
from agent.tools._results import err, is_error


# Effectively unlimited - set to 100000 by default (can be overridden via env var)
MAX_TOOL_TURNS = int(getenv("KRYTH_MAX_TOOL_TURNS", "100000"))
COMPACT_AT_TOKENS = 40000   # compress aggressively — prevents token overflow
KEEP_RECENT_AFTER_COMPACT = 12


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


# Legacy sentinel constants — retained as module attributes so any third
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
        ui.tool_error(f"bad tool args · {e}")
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
        ui.tool_error(f"repeat-denial stop · {tool_name} (×{denial_count})")
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
        ui.tool_error(f"plan-mode block · {tool_name}")
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
                f"Stop retrying with the same arguments — try a "
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
                f"Stop retrying with the same arguments — adjust the "
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
    result = execute_tool(tool_name, args)
    session.tool_call_count += 1
    _clear_denials(session, tool_name)

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
                ui.muted(f"(context: compressed {dropped:,} chars of old browser results)")
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

    high:    references an error or a focal file → keep intact.
    medium:  meaningful payload (>50 chars) → light truncation.
    low:     bulky tool output with no clear signal → aggressive stub.
    """
    text = str(msg.get("content") or "")
    low = text.lower()

    if any(needle in low for needle in _HIGH_SIGNAL_NEEDLES):
        return "high"
    if focal_files and any(f in text for f in focal_files):
        return "high"
    if len(text) < 250:
        return "high"  # already concise — no point eliding
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
                    "content": f"{head}\n…[trimmed]…\n{tail}",
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
                    "content": f"{head}\n…[trimmed]…\n{tail}",
                })
                continue
            # low — keep only the stub.
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
                    new["content"] = text[:600] + " …[trimmed]… " + text[-200:]
                elif tier == "low" and len(text) > 400:
                    dropped_messages += 1
                    dropped_chars += len(text) - 400
                    new["content"] = text[:400] + " …"
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

    summary = summarize(to_compress)
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


def _detect_tool_loop(tool_calls, recent_history, max_repeats=4):
    """Detect repeated identical tool calls to prevent infinite loops.

    Returns True if a loop is detected (same tool called repeatedly).
    """
    if not tool_calls or not recent_history:
        return False

    # Get current tool names
    current_tools = [call.get("function", {}).get("name", "") for call in tool_calls]

    # Track consecutive identical tool calls
    for tool_name in current_tools:
        if not tool_name:
            continue

        # Count how many times this tool appears in recent history
        consecutive_count = 0
        for msg in reversed(recent_history[-10:]):  # Check last 10 messages
            if msg.get("role") == "tool" and msg.get("name") == tool_name:
                consecutive_count += 1
            elif msg.get("role") == "assistant":
                # Reset count if assistant message in between
                break

        if consecutive_count >= max_repeats:
            return True

    return False


def _process_tool_calls(session, tool_calls):
    """Dispatch tool calls; absorb Ctrl+C per call so the loop survives."""
    # Cap tool calls to prevent runaway loops
    if len(tool_calls) > 25:
        ui.warn(f"Tool call limit exceeded ({len(tool_calls)} calls), capping at 25")
        tool_calls = tool_calls[:25]

    for call in tool_calls:
        try:
            dispatch_tool_call(session, call)
        except KeyboardInterrupt:
            ui.tool_cancelled()
            session.append({
                "role": "tool",
                "tool_call_id": call.get("id") or "",
                "name": call.get("function", {}).get("name", ""),
                "content": err("EXEC_FAILED", "tool cancelled by user (Ctrl+C)"),
            })


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


def run_inner_loop(session, max_turns: int, *, verbose_usage: bool = True) -> LoopResult:
    turn_count = 0
    for _ in range(max_turns):
        turn_count += 1
        maybe_compact(session)

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
            if reason in ("api_error", "stream_error", "timeout"):
                return LoopResult(status="api_error", turns_used=turn_count)
            return LoopResult(status="interrupted", turns_used=turn_count)

        session.append(_build_assistant_msg(response))

        tool_calls = response["tool_calls"] or []
        if not tool_calls:
            return LoopResult(
                status="done",
                content=response["content"] or "",
                turns_used=turn_count,
            )

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
                "role": "user",
                "content": (
                    "[system] Tool call completed. Continue with the next step "
                    "of the task. Keep calling tools until the task is fully done."
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
            ui.muted("Graph built · file watcher started")
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
                ui.muted(f"(graph context: {len(files)} relevant files)")
    except Exception:
        pass

    if not graph_context:
        # Fallback: snapshot + focused map
        snapshot = ProjectSnapshot()
        project_map, from_cache = snapshot.get_or_build()
        if from_cache:
            ui.muted("(using cached project snapshot)")
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
            from agent.ecosystem.router import get_router
            from agent.ecosystem.executor import run_skill_workflow
            router = get_router()
            if router.is_build_request(user_input):
                skill_ids = router.route(user_input, use_llm=True)
                if skill_ids:
                    ui.auto_skills(skill_ids)
                    ecosystem_context = run_skill_workflow(
                        skill_ids, user_input, show_progress=True
                    )
        except Exception:
            pass

        if ecosystem_context:
            session.append({"role": "system", "content": ecosystem_context})
        else:
            from agent.skills import auto_select_skills, compose_skills
            auto = auto_select_skills(
                user_input,
                project_context=getattr(session, "project_map", ""),
            )
            if auto:
                ui.auto_skills(auto)
                session.append({"role": "system", "content": compose_skills(auto)})

    # --- Parallel multi-agent build ---
    try:
        from agent.parallel_builder import run_parallel_build
        if session.mode != "plan":
            parallel_result = run_parallel_build(
                user_input,
                skill_context=ecosystem_context or "",
                project_context=getattr(session, "project_map", ""),
                max_turns_per_agent=60,
            )
            if parallel_result:
                session.append({"role": "user", "content": user_input})
                session.append({"role": "assistant", "content": parallel_result})
                _result = LoopResult(status="done", content=parallel_result, turns_used=0)
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
    except Exception as _pe:
        ui.muted(f"(parallel builder skipped: {_pe})")

    plan_dict: dict | None = None
    plan_prose: str = ""
    # Skip the planner for short/simple prompts — it would waste an LLM call
    # on something the main model can handle directly. A prompt needs at least
    # 6 words to benefit from planning; single-agent starters (fix/debug/explain)
    # are also fast-tracked.
    _skip_planner = False
    try:
        from agent.parallel_builder import _quick_reject
        _skip_planner = _quick_reject(user_input)
    except Exception:
        _skip_planner = len(user_input.strip().split()) < 6
    if _should_plan(user_input) and not _skip_planner:
        plan_dict, plan_prose = ask_planner(user_input)
        if plan_dict:
            ui.plan(plan_dict)
        elif plan_prose:
            ui.plan_prose(plan_prose)

    if session.mode == "plan":
        ui.plan_mode_active()

    user_content = user_input
    if plan_dict:
        user_content = f"{user_input}\n\n[plan]\n{_plan_hint_for_model(plan_dict)}"
    elif plan_prose:
        user_content = f"{user_input}\n\n[planner hint] {plan_prose}"
    session.append({"role": "user", "content": user_content})

    result = run_inner_loop(session, MAX_TOOL_TURNS, verbose_usage=True)

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
