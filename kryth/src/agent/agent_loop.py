"""Agent loop: orchestration of the tool-use cycle.

This module is the brain: it asks the model what to do, dispatches the
tools the model asks for, captures the results, and repeats. Every byte
of user-facing output is emitted as an event on ``agent.ui`` --- this
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
# from agent.ecosystem.router import route  # removed: skills auto-injection disabled
from agent.env import getenv
from agent.hooks import HOOK_BLOCK_PREFIX, run_hooks
from agent.llm import ask_llm_stream, ask_planner, summarize, _sanitize_tool_name
from agent.permissions import ask_user, check_permission
from agent.project_context import git_status_snapshot, load_context_file
from agent.prompts import SYSTEM_PROMPT
from agent.session import get_session
# from agent.skills import auto_select_skills, compose_skills  # removed: skills auto-injection disabled

from agent.tools import (
    READ_ONLY_TOOLS,
    RUN_COMMAND_ERROR_MARKER,
    SELF_RENDERED_TOOLS,
    TOOL_SPECS,
    TOOLS,
)
from agent.tools._results import err, is_error

# Orchestration removed — single-agent sequential execution only.


# Effectively unlimited - set to 100000 by default (can be overridden via env var)
MAX_TOOL_TURNS = int(getenv("KRYTH_MAX_TOOL_TURNS", "100000"))
COMPACT_AT_TOKENS = 30000   # ~90k real tokens --- compact less often, fewer LLM round-trips
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
    finish_reason: str = ""  # last LLM finish_reason (timeout/api_error/etc.)

    @property
    def incomplete(self) -> bool:
        """True when the agent stopped before finishing the task."""
        return self.status != "done"


# Legacy sentinel constants --- retained as module attributes so any third
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
    # Defensive chokepoint: clean any Harmony/special-token bleed in the
    # function name before it reaches label(), the registry, or hooks.
    tool_name = _sanitize_tool_name(fn.get("name", ""))
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
        ui.tool_error(f"bad tool args --- {e}")
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
        ui.tool_error(f"repeat-denial stop --- {tool_name} (--{denial_count})")
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
        ui.tool_error(f"plan-mode block --- {tool_name}")
        _append_tool_msg(session, call_id, tool_name, result)
        return

    # ---- Permission gate ----------------------------------------
    decision = check_permission(tool_name, args, session=session)
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
                f"Stop retrying with the same arguments --- try a "
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
                f"Stop retrying with the same arguments --- adjust the "
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

    # Nonsense-command guard: reject run_command calls that contain code
    # snippets, markdown, or non-shell syntax. The model sometimes emits
    # these when it should have answered in text instead of calling tools.
    if tool_name == "run_command":
        _cmd = args.get("command", "") or ""
        _nonsense_indicators = (
            "const ", "let ", "var ", "function ", "=>",
            "async ", "await ", "import ", "export ",
            "```", "#!/", "<!--", "<?php",
        )
        _has_nonsense = any(kw in _cmd for kw in _nonsense_indicators)
        _has_non_ascii = bool(re.search(r"[^\x00-\x7F\s]", _cmd[:40]))
        if _has_nonsense or _has_non_ascii:
            _block_msg = (
                f"[COMMAND BLOCKED — not a valid shell command]\n"
                f"The command contains code/markdown syntax, not shell. "
                f"Answer the user's question in text instead of running tools."
            )
            ui.tool_start(tool_name, args)
            ui.tool_error(f"blocked nonsensical command: {_cmd[:60]!r}")
            _append_tool_msg(session, call_id, tool_name, _block_msg)
            return

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

    _tool_start_ts = __import__("time").monotonic()
    result = execute_tool(tool_name, args)
    _tool_elapsed = __import__("time").monotonic() - _tool_start_ts
    session.tool_call_count += 1
    _clear_denials(session, tool_name)

    # V1.6: record timing for anti-paralysis metrics
    try:
        from agent.anti_paralysis import record_timing as _ap_timing
        _ap_timing(id(session), tool_name, _tool_elapsed)
    except Exception:
        pass

    # V1.6: update mission memory when file written
    if tool_name == "write_file":
        try:
            from agent.anti_paralysis import get_memory as _ap_mem
            _ap_mem(id(session)).remember_fix(
                f"wrote {args.get('path', '')}"
            )
        except Exception:
            pass

    # Anti-paralysis: detect test success to enable stop-after-success (Phase 8)
    if tool_name in ("run_tests", "run_command"):
        _result_str = str(result)
        _cmd = args.get("command", "") or ""
        _is_test_cmd = any(t in _cmd for t in ("pytest", "jest", "npm test", "yarn test", "vitest"))
        if tool_name == "run_tests" or _is_test_cmd:
            if ("passed" in _result_str.lower() or "ok" in _result_str.lower()) \
               and "failed" not in _result_str.lower() \
               and "error" not in _result_str.lower()[:100]:
                try:
                    from agent.anti_paralysis import record_tests_passed
                    record_tests_passed(id(session))
                except Exception:
                    pass

    # Rules 17+18: background validation + incremental testing after every write.
    # Delegates to patch_pipeline for centralized syntax/lint/test checks.
    # All jobs are daemon threads --- never block the mission.
    if tool_name == "write_file":
        _written_path = args.get("path", "")
        if _written_path:
            
            def _bg_validate_and_test(path: str, complexity: str) -> None:
                try:
                    from agent.patch_pipeline import validate_patch_silent
                    validate_patch_silent(path, complexity=complexity)
                except Exception:
                    pass
            import threading as _bgt
            _bgt.Thread(target=_bg_validate_and_test,
                        args=(_written_path, "medium"),
                        daemon=True, name=f"kryth-validate-{_written_path[-20:]}").start()

    # Push rich events to floating Textual dashboard
    try:
        import sys as _sys
        _dash = _sys.modules.get("agent.ui.dashboard")
        if _dash is not None and _dash.get_active():
            _agent_role = getattr(session, "_agent_role", "")
            _agent_id = getattr(session, "_agent_id", _agent_role)
            _path = (args.get("path") or args.get("command", ""))[:35]

            # Base tool event — pass the file/target as `detail` so the
            # dashboard can humanize it ("Creating hero.tsx", not "write_file").
            _dash.push_event("tool_used", tool=tool_name, agent=_agent_role,
                             action=f"{tool_name} {_path}".strip(), detail=_path)

            # Rich tool stream with icons
            _icons = {
                "read_file": "---", "write_file": "---", "edit_file": "---",
                "multi_edit": "---", "run_command": "---", "grep": "---",
                "search_code": "---", "semantic_search": "---",
                "browser_click": "---", "browser_type": "---",
                "run_tests": "---",
            }
            _icon = _icons.get(tool_name, "-")
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

    # Phase 3 — smart output summarizer: compress large terminal / test / install outputs
    try:
        from agent.output_summarizer import summarize as _os_summarize
        _os_compressed, _os_raw_n, _os_out_n = _os_summarize(tool_name, result_str)
        if _os_out_n < _os_raw_n:
            saved = _os_raw_n - _os_out_n
            result_str = _os_compressed
            if saved > 500:
                ui.debug(f"(output-summarizer: {_os_raw_n:,}→{_os_out_n:,} chars, -{saved//4} tok)")
    except Exception:
        pass

    # Auto-compress browser results every N tool calls
    try:
        from agent.context_manager import compress_messages, COMPRESS_EVERY_N
        if session.tool_call_count % COMPRESS_EVERY_N == 0:
            session.messages, dropped = compress_messages(session.messages)
            if dropped > 0:
                ui.debug(f"(context: compressed {dropped:,} chars of old browser results)")
    except Exception:
        pass

    # Periodic history checkpointing — compresses old turns into structured JSON
    try:
        from agent.checkpoint_manager import should_checkpoint, apply_checkpoint
        _ckpt_count = getattr(session, "_tool_calls_since_checkpoint", 0) + 1
        session._tool_calls_since_checkpoint = _ckpt_count
        if should_checkpoint(session, _ckpt_count):
            session.messages, _freed = apply_checkpoint(session.messages)
            session._tool_calls_since_checkpoint = 0
            if _freed > 0:
                ui.debug(f"(checkpoint: archived {_freed:,} chars of old history)")
    except Exception:
        pass

    # Tools that render their own visual representation skip the generic tee
    if tool_name not in SELF_RENDERED_TOOLS:
        ui.tool_result(result_str, error=has_error(result_str))
    _append_tool_msg(session, call_id, tool_name, result_str)


# ------ Rule 21: keyword --- file pattern map used by speculative preload ------------------------------
_KEYWORD_PRELOAD_MAP: dict[str, list[str]] = {
    "auth":     ["jwt", "middleware", "session", "security", "login", "oauth", "token"],
    "database": ["models", "migrations", "schema", "orm", "db", "repository"],
    "frontend": ["components", "css", "routes", "ui", "styles", "hooks", "pages"],
    "api":      ["routes", "endpoints", "handlers", "controllers", "views"],
    "test":     ["tests", "spec", "fixtures", "conftest", "jest", "pytest"],
    "deploy":   ["Dockerfile", "docker-compose", ".github", "ci", "helm", "k8s"],
    "payment":  ["billing", "stripe", "webhook", "invoice", "subscription"],
    "cache":    ["redis", "memcached", "cache", "ttl"],
    "websocket":["ws", "socket", "realtime", "event"],
    "email":    ["smtp", "sendgrid", "mailgun", "template", "notification"],
}

_IGNORE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv", "dist",
    "build", ".next", ".turbo", "coverage", ".pytest_cache",
})


def _speculative_preload(session, user_input: str) -> "threading.Event":
    """Fire background threads immediately to pre-load memory + files.

    Rules 13 (memory), 14 (repo scan), 21 (keyword prefetch).
    Returns an Event that is set when all background tasks complete.
    The caller should wait at most ~200 ms then inject whatever is ready.
    """
    
    import os as _os
    import threading as _t

    done = _t.Event()
    results: dict = {}
    session._speculative_results = results

    lower = user_input.lower()
    patterns: list[str] = []
    for keyword, pats in _KEYWORD_PRELOAD_MAP.items():
        if keyword in lower:
            patterns.extend(pats)
    # dedupe preserving first-seen order
    seen: set[str] = set()
    patterns = [p for p in patterns if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]

    pending = [1 + (1 if patterns else 0)]
    lock = _t.Lock()

    def _tick() -> None:
        with lock:
            pending[0] -= 1
            if pending[0] == 0:
                done.set()

    def _search_memory() -> None:
        try:
            from agent.experience import get_experience
            exp = get_experience(".")
            hits = exp.search(user_input, top_k=3)
            if hits:
                results["experience"] = hits
                session._experience_hits = getattr(session, "_experience_hits", 0) + len(hits)
        except Exception:
            pass
        finally:
            _tick()

    def _preload_files() -> None:
        try:
            found: list[str] = []
            for dirpath, dirnames, filenames in _os.walk("."):
                dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
                for fname in filenames:
                    fpath = _os.path.join(dirpath, fname)
                    if any(p.lower() in fname.lower() or p.lower() in fpath.lower()
                           for p in patterns):
                        try:
                            if _os.path.getsize(fpath) < 30_000:
                                found.append(fpath)
                        except OSError:
                            pass
                if len(found) >= 12:
                    break
            if found:
                results["files"] = found[:12]
        except Exception:
            pass
        finally:
            _tick()

    _t.Thread(target=_search_memory, daemon=True, name="kryth-mem-preload").start()
    if patterns:
        _t.Thread(target=_preload_files, daemon=True, name="kryth-file-preload").start()
    else:
        done.set()  # no file patterns needed --- only memory thread outstanding; it will set

    return done


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

    high:    references an error or a focal file --- keep intact.
    medium:  meaningful payload (>50 chars) --- light truncation.
    low:     bulky tool output with no clear signal --- aggressive stub.
    """
    text = str(msg.get("content") or "")
    low = text.lower()

    if any(needle in low for needle in _HIGH_SIGNAL_NEEDLES):
        return "high"
    if focal_files and any(f in text for f in focal_files):
        return "high"
    if len(text) < 250:
        return "high"  # already concise --- no point eliding
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
                    "content": f"{head}\n---[trimmed]---\n{tail}",
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
                    "content": f"{head}\n---[trimmed]---\n{tail}",
                })
                continue
            # low --- keep only the stub.
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
                    new["content"] = text[:600] + " ---[trimmed]--- " + text[-200:]
                elif tier == "low" and len(text) > 400:
                    dropped_messages += 1
                    dropped_chars += len(text) - 400
                    new["content"] = text[:400] + " ---"
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

    # Subagents (depth > 0) skip the LLM summarizer --- they have a short focused
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

    Write/edit tools are explicitly excluded --- calling write_file 20 times is
    normal when building a project. Only read-only or search tools looping with
    no progress signal a real loop.

    Special case: run_command with identical args repeated 5+ times is always
    a loop regardless --- catches infinite pip install / pytest retry loops.
    """
    if not tool_calls or not recent_history:
        return False

    # These tools are safe to repeat many times --- never flag them as loops.
    _REPEAT_OK = frozenset({
        "write_file", "edit_file", "multi_edit",
        "read_file", "grep", "glob", "search_code",
        "browser_click", "browser_type", "browser_scroll",
        "todo_write", "checkpoint",
    })

    import json as _json

    current_tools = [call.get("function", {}).get("name", "") for call in tool_calls]

    # Special: run_command with the same args 5+ times in the last 20 messages = loop.
    for call in tool_calls:
        if call.get("function", {}).get("name") == "run_command":
            try:
                args = call.get("function", {}).get("arguments", "")
                args_obj = _json.loads(args) if isinstance(args, str) else args
                cmd = args_obj.get("command", "") if isinstance(args_obj, dict) else ""
            except Exception:
                cmd = ""
            if not cmd:
                continue
            count = 0
            for msg in reversed(recent_history[-20:]):
                if msg.get("role") == "tool" and msg.get("name") == "run_command":
                    count += 1
                elif msg.get("role") == "assistant":
                    # Check if this assistant turn called the same command
                    for tc in (msg.get("tool_calls") or []):
                        tc_args = tc.get("function", {}).get("arguments", "")
                        try:
                            tc_obj = _json.loads(tc_args) if isinstance(tc_args, str) else tc_args
                            tc_cmd = tc_obj.get("command", "") if isinstance(tc_obj, dict) else ""
                        except Exception:
                            tc_cmd = ""
                        if tc_cmd == cmd:
                            count += 1
            if count >= 5:
                return True

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


def _expand_run_command_paths(tool_calls: list) -> None:
    """When write_file and run_command appear in the same batch, auto-expand
    bare filenames in run_command to the full absolute path from write_file.

    Prevents the common failure: model writes to /abs/path/file.py but runs
    `python file.py` (relative) from a different cwd → exit 2 / FileNotFoundError.
    This fires before any tool executes, so all calls get the fix.
    """
    import os as _os2
    written: dict[str, str] = {}
    for call in tool_calls:
        fn = call.get("function", {})
        if fn.get("name") == "write_file":
            try:
                a = json.loads(fn.get("arguments", "{}") or "{}")
                p = a.get("path", "")
                if p:
                    written[_os2.path.basename(p).lower()] = _os2.path.abspath(p)
            except Exception:
                pass
    if not written:
        return
    for call in tool_calls:
        fn = call.get("function", {})
        if fn.get("name") != "run_command":
            continue
        try:
            raw = fn.get("arguments", "{}") or "{}"
            a = json.loads(raw)
            cmd = (a.get("command") or "").strip()
            if not cmd:
                continue
            # Match: <runner> [optional flags] <bare-filename.ext> [rest]
            # bare-filename = no path separator chars
            m = re.match(
                r'^([\w./-]+(?:\s+[\w./-]*)*?)\s+(["\']?)([\w][^/\\]*\.\w+)\2(.*)$',
                cmd,
            )
            if not m:
                continue
            runner, _q, fname, rest = m.group(1), m.group(2), m.group(3), m.group(4)
            abs_p = written.get(fname.lower())
            # Only rewrite if: fname has no path separators and we wrote that file
            if abs_p and _os2.sep not in fname and "/" not in fname:
                a["command"] = f'{runner} "{abs_p}"{rest}'
                fn["arguments"] = json.dumps(a)
        except Exception:
            pass


def _process_tool_calls(session, tool_calls):
    """Dispatch tool calls sequentially.

    All tool calls are executed one after another in sequence.
    """
    _expand_run_command_paths(tool_calls)

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

    # Execute all tool calls sequentially
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
      - 'user' follows 'tool' directly (must be tool --- assistant --- user)
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
    import time as _time
    turn_count = 0
    _consecutive_no_tool_turns = 0
    _total_tool_calls = 0
    _api_retries = 0
    # Subagents (depth > 0) are workers with a clear task --- allow fewer
    # idle turns before declaring done, cutting wasted LLM round-trips.
    _is_subagent = getattr(session, "depth", 0) > 0
    _start_ts = getenv("_KRYTH_LOOP_TS", "")  # for elapsed-time display
    _consecutive_cmd_failures = 0  # track back-to-back run_command failures
    # Rule 23: performance counters
    _loop_start = _time.monotonic()
    _total_planning_s: float = 0.0
    _total_executing_s: float = 0.0

    # Anti-paralysis tracking — session-scoped, opt-out via KRYTH_NO_ANTIPARALYSIS=1
    _ap_session_id = id(session)
    _ap_enabled = getenv("KRYTH_NO_ANTIPARALYSIS", "0") not in ("1", "true", "yes")
    if _ap_enabled:
        try:
            from agent.anti_paralysis import record_tool_call as _ap_record
            from agent.anti_paralysis import should_stop as _ap_should_stop
            from agent.anti_paralysis import stop_after_success_nudge as _ap_stop_nudge
            from agent.anti_paralysis import format_metrics as _ap_metrics
        except Exception:
            _ap_enabled = False

    # Context supervisor replaces maybe_compact --- tiered budget-aware compression
    try:
        from agent.context_supervisor import ContextSupervisor
        _supervisor: ContextSupervisor | None = ContextSupervisor(session)
    except Exception:
        _supervisor = None



    # Checkpoint tracking — how many tool calls since last checkpoint
    _tool_calls_since_checkpoint = 0

    for _ in range(max_turns):
        turn_count += 1
        # Rule 22: synchronous context supervision (compression already done
        # or does it now if threshold crossed --- fast when under budget).
        if _supervisor is not None:
            _supervisor.check()
        else:
            maybe_compact(session)
        _enforce_message_order(session)

        # Emit live progress for subagents so the UI shows current state.
        if _is_subagent and turn_count > 1:
            role = getattr(session, "_agent_role", "")
            label = f"--- {role} - turn {turn_count}/{max_turns}" if role else f"--- turn {turn_count}/{max_turns}"
            ui.llm_waiting(label)

        # ── Tool curation ────────────────────────────────────────────────
        # Send only the tools relevant to this task (huge token saving). If a
        # prior turn escalated (the model needed a tool we hadn't offered), we
        # fall back to the full set for the rest of the session — so curation
        # can never permanently break execution.
        _turn_tools = TOOL_SPECS
        if not getattr(session, "_tools_full_escalated", False):
            try:
                from agent.tool_curator import curate
                _turn_tools = curate(session.messages, TOOL_SPECS)
            except Exception:
                _turn_tools = TOOL_SPECS

        # Hard token budget gate + pre-call telemetry
        try:
            from agent.token_budget import check as _budget_check
            _budget_result = _budget_check(
                session.messages,
                _turn_tools,
                "medium",
                session=session,
                auto_compress=True,
            )
            ui.token_budget(
                est_before=_budget_result.estimated,
                tools_tok=_budget_result.tools_tok,
                history_tok=_budget_result.history_tok,
                tools_count=len(_turn_tools),
            )
        except Exception:
            # Fallback: plain telemetry without budget gate
            try:
                _hist_chars = sum(len(str(m.get("content") or "")) for m in session.messages)
                _tools_chars = sum(len(str(s)) for s in _turn_tools)
                _est_before = (_hist_chars + _tools_chars) // 4
                ui.token_budget(
                    est_before=_est_before,
                    tools_tok=_tools_chars // 4,
                    history_tok=_hist_chars // 4,
                    tools_count=len(_turn_tools),
                )
            except Exception:
                pass

        _turn_llm_start = _time.monotonic()
        response = ask_llm_stream(
            session.messages,
            tools=_turn_tools,
        )
        _turn_llm_end = _time.monotonic()
        _total_planning_s += _turn_llm_end - _turn_llm_start
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
                # Input too large --- force compaction and retry this turn.
                ui.muted("(context overflow --- compacting and retrying---)")
                maybe_compact(session)
                turn_count -= 1  # don't count this as a used turn
                continue
            if reason in ("api_error", "stream_error", "timeout"):
                # Retry policy is configurable. Default: ONE quick retry then
                # surface the error — no slow 20/40/60s storm. Set
                # KRYTH_API_MAX_RETRIES=0 to fail immediately, or raise it for
                # flaky networks. KRYTH_API_RETRY_DELAY sets the base delay (s).
                import os as _os, time as _t2
                _max_retries = int(_os.environ.get("KRYTH_API_MAX_RETRIES", "1"))
                if _api_retries < _max_retries:
                    _api_retries += 1
                    _retry_delay = int(_os.environ.get("KRYTH_API_RETRY_DELAY", "3"))
                    ui.muted(f"(api error — retry {_api_retries}/{_max_retries} in {_retry_delay}s)")
                    _t2.sleep(_retry_delay)
                    turn_count -= 1  # replay this turn
                    continue
                _fr = {"stream_error": "malformed", "timeout": "timeout"}.get(reason, "api_error")
                return LoopResult(status="api_error", turns_used=turn_count, finish_reason=_fr)
            return LoopResult(status="interrupted", turns_used=turn_count, finish_reason=reason or "interrupted")

        _api_retries = 0  # reset on successful LLM response
        session.append(_build_assistant_msg(response))

        # finish_reason == "length" means the model hit the OUTPUT token cap
        # mid-generation (not a context overflow). Tell the llm layer to cache
        # a higher limit for this model so the next call uses more output tokens,
        # then silently pop the truncated message and retry --- no compaction needed.
        if response.get("finish_reason") == "length":
            # Output token cap hit --- bump the cached limit and retry silently.
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
            content = response["content"] or ""

            # ── Safety refusal early-exit ─────────────────────────────────
            # When the model explicitly refuses a request ("I cannot", "I will
            # not", "I must refuse"), exit immediately. Nudging after a refusal
            # causes the model to reverse its decision, which is the root cause
            # of safety_system32 hanging — it refuses twice then gets nudged
            # into executing.
            _REFUSAL_SIGNALS = (
                "i cannot", "i will not", "i won't", "i am unable",
                "i'm unable", "i must refuse", "i refuse", "i can't",
                "cannot execute", "cannot perform", "cannot help with",
                "will not execute", "will not perform", "not something i",
                "not able to", "unable to help", "unable to assist",
                "dangerous", "would be destructive", "could damage",
                "would destroy", "not safe", "safety concern",
            )
            _content_lower = content.lower()
            if any(sig in _content_lower for sig in _REFUSAL_SIGNALS):
                return LoopResult(status="done", content=content, turns_used=turn_count, finish_reason="refused")

            # ── V1.6 Phase 3: impl mode nudge injection ──────────────────
            if _ap_enabled:
                try:
                    from agent.anti_paralysis import should_inject_impl_nudge, impl_mode_nudge, get_root_cause
                    if should_inject_impl_nudge(_ap_session_id, had_tool_calls=False):
                        _rc = get_root_cause(_ap_session_id)
                        session.append({"role": "user", "content": impl_mode_nudge(_rc)})
                        turn_count -= 1  # replay with nudge
                        continue
                except Exception:
                    pass

            # ── Curation auto-expand (Phase 6 safety) ────────────────────
            # If we offered a curated SUBSET and the model produced no tool
            # call on an execution task that hasn't progressed, it may lack a
            # tool it needs. Escalate ONCE to the full tool set and replay the
            # turn — guaranteeing curation can never starve execution.
            # SKIP escalation when the model gave a text-only answer — that
            # means it chose not to use tools, not that it lacked the right one.
            if (
                _total_tool_calls == 0
                and len(_turn_tools) < len(TOOL_SPECS)
                and not getattr(session, "_tools_full_escalated", False)
                and not content
            ):
                session._tools_full_escalated = True
                try:
                    from agent.tool_curator import _curation_miss
                    _curation_miss()
                except Exception:
                    pass
                if session.messages and session.messages[-1].get("role") == "assistant":
                    session.messages.pop()
                turn_count -= 1  # replay with the full tool set
                continue

            # Subagents stop on no-tool response --- BUT if they haven't called
            # any tools yet, nudge once to get them started before exiting.
            if _is_subagent:
                if _total_tool_calls == 0 and _consecutive_no_tool_turns == 0:
                    _consecutive_no_tool_turns += 1
                    session.append({
                        "role": "user",
                        "content": "[sys] Call tools now. BUILD: write_file all files. FIX: read_file target.",
                    })
                    continue
                return LoopResult(status="done", content=content, turns_used=turn_count, finish_reason="completed")

            _consecutive_no_tool_turns += 1

            # Text-only (no tool calls) completion path.
            # No tool calls = the model is either answering a question
            # conversationally, or it finished its tool work and is
            # summarizing. Accept as done.
            # Note: content may be empty in the response dict even when
            # the LLM streamed text live (common with step/nemotron models
            # where content goes through _filter_leaks). The streaming UI
            # already displayed it to the user, so an empty content string
            # here is fine — we know the model didn't want tools.
            if _total_tool_calls == 0:
                return LoopResult(status="done", content=content, turns_used=turn_count, finish_reason="completed")

            if _consecutive_no_tool_turns <= 2 and _total_tool_calls > 0:
                _files_written = sum(
                    1 for m in session.messages
                    if m.get("role") == "tool" and m.get("name") == "write_file"
                )
                _cmds_run = sum(
                    1 for m in session.messages
                    if m.get("role") == "tool" and m.get("name") == "run_command"
                    and not (m.get("content") or "").startswith("[COMMAND BLOCKED")
                )
                _cmds_blocked = sum(
                    1 for m in session.messages
                    if m.get("role") == "tool" and m.get("name") == "run_command"
                    and (m.get("content") or "").startswith("[COMMAND BLOCKED")
                )
                if _files_written > 0 and _cmds_run == 0:
                    _nudge = f"[sys] {_files_written} file(s) written. Run install+start now."
                elif _files_written == 0 and _cmds_run == 0:
                    return LoopResult(status="done", content=content or "", turns_used=turn_count, finish_reason="completed")
                elif _files_written == 0 and _cmds_blocked > 0 and _cmds_run == 0:
                    return LoopResult(status="done", content=content or "", turns_used=turn_count, finish_reason="completed")
                else:
                    _nudge = "[sys] Run install+start now, or reply with your final answer."
                session.append({"role": "user", "content": _nudge})
                continue

            # Self-evaluation gate: for non-trivial tasks, score confidence
            # before declaring done. Low confidence → one extra validation turn.
            if _total_tool_calls > 0:
                try:
                    from agent.self_eval import evaluate_task as _self_eval
                    _task_desc = next(
                        (m.get("content", "") for m in session.messages
                         if m.get("role") == "user" and isinstance(m.get("content"), str)),
                        "",
                    )
                    _ev = _self_eval(session, _task_desc, "medium")
                    if not _ev.confident and turn_count <= max_turns - 2 \
                            and not getattr(session, "_self_eval_fired", False):
                        session._self_eval_fired = True
                        session.append({"role": "user", "content": _ev.nudge_message()})
                        continue
                except Exception:
                    pass

            return LoopResult(
                status="done",
                content=content,
                turns_used=turn_count,
                finish_reason="completed",
            )

        # Tools were called --- reset the no-tool counter.
        _consecutive_no_tool_turns = 0
        _total_tool_calls += len(tool_calls)

        # Anti-paralysis: record tool calls, check execution budget and stop signal
        if _ap_enabled:
            try:
                for _tc in tool_calls:
                    _tname = (_tc.get("function") or {}).get("name", "")
                    _ap_nudge = _ap_record(_ap_session_id, _tname, "medium")
                    if _ap_nudge:
                        # Budget exhausted — inject nudge and continue to next turn
                        session.append({"role": "user", "content": _ap_nudge})
                        break
                # Phase 8: stop after success (files written + tests passed)
                if _ap_should_stop(_ap_session_id):
                    _stop_msg = _ap_stop_nudge()
                    session.append({"role": "user", "content": _stop_msg})
                    ui.muted(_ap_metrics(_ap_session_id))
            except Exception:
                pass

        # Detect infinite tool loops
        hermes_recovered = response.get("finish_reason") in (
            "recovered_after_stream_error", None
        ) and any(
            (tc.get("id") or "").startswith("hermes_") for tc in tool_calls
        )
        # Question-intent guard: if the user's original message is a question
        # and there are already file read results in context, block execution
        # tools (write_file, run_command, edit_file) so the model answers
        # from context. Prevents "what is incomplete?" → pip install → python.
        _user_msg = ""
        for _m in session.messages:
            if _m.get("role") == "user" and isinstance(_m.get("content"), str):
                _user_msg = _m["content"]
                break
        _question_signals = (
            "what ", "what's", "how ", "how's", "why ", "why's",
            "where ", "when ", "who ", "which ", "is ", "are ",
            "can ", "does ", "do ", "will ", "would ", "should ",
            "incomplete", "missing", "wrong", "broken", "issues",
            "explain", "summar", "describe", "tell me", "list ",
        )
        _is_question = any(s in _user_msg.lower() for s in _question_signals)
        if _is_question:
            _has_reads = any(
                m.get("role") == "tool" and m.get("name") in ("read_file", "grep")
                for m in session.messages
            )
            if _has_reads:
                _exec_tools = [tc for tc in tool_calls
                               if (tc.get("function") or {}).get("name", "")
                               in ("write_file", "edit_file", "run_command", "multi_edit")]
                if _exec_tools:
                    _n_exec = len(_exec_tools)
                    _read_only = [tc for tc in tool_calls
                                  if (tc.get("function") or {}).get("name", "")
                                  not in ("write_file", "edit_file", "run_command", "multi_edit")]
                    if _read_only:
                        tool_calls = _read_only
                    else:
                        session.append({"role": "user", "content":
                            "[sys] The user asked a question. You already have the files in context above. "
                            "Answer the question in text — do NOT run commands or write files."})
                        turn_count -= 1
                        continue

        _exec_start = _time.monotonic()
        _process_tool_calls(session, tool_calls)
        _total_executing_s += _time.monotonic() - _exec_start

        # Rule 23: push live perf metrics to dashboard every tool turn
        try:
            import sys as _sys
            _dash = _sys.modules.get("agent.ui.dashboard")
            if _dash is not None and _dash.get_active():
                _elapsed = _time.monotonic() - _loop_start
                _dash.push_event("perf_metrics",
                    planning_s=round(_total_planning_s, 1),
                    executing_s=round(_total_executing_s, 1),
                    elapsed_s=round(_elapsed, 1),
                    total_tools=_total_tool_calls,
                    turn=turn_count,
                )
        except Exception:
            pass

        # Safety gate early-exit: if the last tool result was a [BLOCKED]
        # destructive-command refusal, exit immediately. Otherwise the model
        # will loop trying alternative deletion commands forever.
        _last_tool_msg = next(
            (m for m in reversed(session.messages) if m.get("role") == "tool"),
            None,
        )
        if _last_tool_msg and "[BLOCKED] Destructive command" in (
            _last_tool_msg.get("content") or ""
        ):
            _blocked_content = next(
                (m.get("content") or "" for m in reversed(session.messages)
                 if m.get("role") == "assistant"
                 and isinstance(m.get("content"), str)
                 and m.get("content")),
                "[BLOCKED] Destructive command requires explicit confirmation — not executed.",
            )
            return LoopResult(
                status="done",
                content=_blocked_content,
                turns_used=turn_count,
                finish_reason="blocked",
            )

        # Detect "failing retry" loops: repeatedly running commands that keep
        # failing with no progress. Applies to BOTH root agent and subagents.
        # Catches the pattern: run X → fail → run Y → fail → run X → fail …
        # (alternating bad commands don't reset each other's failure count).
        _recent_tool_results = [
            m for m in session.messages[-12:] if m.get("role") == "tool"
        ]
        if len(_recent_tool_results) >= 3:
            # A tool message is a failure when its content starts with "[ERROR "
            # (the TOOL_ERROR_PREFIX). UI strings like "Status Failed" are not
            # present in message content — only in the rendered terminal output.
            _fail_count = sum(
                1 for m in _recent_tool_results
                if (m.get("content") or "").startswith("[ERROR ")
            )
            _threshold = 5
            if _fail_count >= _threshold:
                ui.warn(
                    f"Stopped: {_fail_count} consecutive command failures with no progress. "
                    "The task completed successfully before the loop started — check the output above."
                )
                # Return the last meaningful assistant content as result
                _last_content = next(
                    (m.get("content", "") for m in reversed(session.messages)
                     if m.get("role") == "assistant" and m.get("content")),
                    "Task complete.",
                )
                return LoopResult(
                    status="done",
                    content=_last_content,
                    turns_used=turn_count,
                    finish_reason="completed",
                )

        # Subagent-specific: stricter failure detection
        if _is_subagent:
            _recent_results = [
                m for m in session.messages[-16:] if m.get("role") == "tool"
            ]
            if len(_recent_results) >= 4:
                _fail_count = sum(
                    1 for m in _recent_results
                    if (m.get("content") or "").startswith("[ERROR ")
                )
                if _fail_count >= 4:
                    ui.warn("Subagent stuck in failure loop --- same errors recurring. Breaking.")
                    return LoopResult(
                        status="interrupted",
                        content="Agent aborted: repeated failures with no progress.",
                        turns_used=turn_count,
                        finish_reason="interrupted",
                    )


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
    if _ap_enabled:
        try:
            ui.muted(_ap_metrics(_ap_session_id))
        except Exception:
            pass
    return LoopResult(status="max_turns", turns_used=turn_count, finish_reason="max_turns")


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
    
    import os as _os

    # --- Auto-init: build graph in background daemon --- never blocks execution ---
    def _bg_graph_init():
        try:
            from agent.memory import memory
            if not memory.graph.is_built() and getenv_bool("KRYTH_AUTO_INIT", True):
                memory.init(auto=True)
        except Exception:
            pass
    import threading as _init_t

    _init_t.Thread(target=_bg_graph_init, daemon=True).start()

    _cwd = _os.getcwd()
    project_map = ""
    project_doc = ""
    git_state = ""

    # --- I/O: project map, context doc, git state ---
    import concurrent.futures as _cf_init

    def _get_project_map():
        # Priority 1: semantic memory graph (built after /init)
        try:
            from agent.memory import memory
            if memory.graph.is_built() and user_input:
                files = memory.graph.search(user_input, top_k=12)
                if files:
                    ctx = memory.graph.context_for(files)
                    if ctx:
                        ui.debug(f"(graph context: {len(files)} relevant files)")
                        return ctx
        except Exception:
            pass

        # Priority 2: grep-first retrieval (Phase 2 — Claude-Code style)
        # Fast, no graph needed. Finds files containing task-relevant symbols.
        try:
            from agent.context import build_retrieval_context
            _retrieval = build_retrieval_context(user_input)
            if _retrieval:
                ui.debug(f"(retrieval context: grep-based)")
                return _retrieval
        except Exception:
            pass

        # Priority 3: mtime-cached focused directory map (fallback)
        snapshot = ProjectSnapshot()
        project_map, from_cache = snapshot.get_or_build()
        if from_cache:
            ui.debug("(using cached project snapshot)")
        if user_input and session.messages:
            project_map = build_focused_map(user_input)
        return project_map

    with _cf_init.ThreadPoolExecutor(max_workers=3, thread_name_prefix="kryth-init") as _pool:
        _map_fut = _pool.submit(_get_project_map)
        _doc_fut = _pool.submit(load_context_file)
        _git_fut = _pool.submit(git_status_snapshot)
        project_map = _map_fut.result()
        project_doc = _doc_fut.result()
        git_state = _git_fut.result()

    session.project_map = project_map

    _sys_prompt = SYSTEM_PROMPT
    parts = [_sys_prompt]
    parts.append(f"CWD: {_cwd}")
    if project_doc:
        parts.append(project_doc)
    if git_state:
        parts.append(git_state)
    if project_map:
        parts.append(f"Project files:\n{project_map}")

    session.system_prompt = "\n\n".join(parts)

    # Token breakdown for debugging
    ui.debug(
        f"  ctx: prompt={len(_sys_prompt)//4} tok"
        f"  mem={len(project_doc)//4} tok"
        f"  git={len(git_state)//4} tok"
        f"  map={len(project_map)//4} tok"
    )


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

    # ── Interrupted-task cleanup ──────────────────────────────────────────
    # If the previous turn was interrupted (Ctrl+C), the session still holds
    # the old task's user message, partial assistant response, and tool results.
    # Leaving them in history causes the LLM to finish the old task before
    # addressing the new one. Strip everything back to system messages only,
    # so the new task starts with a clean slate.
    if getattr(session, "_task_interrupted", False):
        session._task_interrupted = False
        session.messages = [
            m for m in session.messages if m.get("role") == "system"
        ]
        ui.muted("  (previous task interrupted — starting fresh)")


    # Rule 15: early worker pool warmup --- fires before LLM call, before build_initial_system.
    # Workers begin reading domain-relevant files while we build project context.
    _early_futures: dict = {}
    try:
        pass
    except Exception:
        pass

    # Rules 13/14/21: fire speculative preload immediately --- runs while project
    # scan and context build happen, so memory + relevant files arrive for free.
    _preload_done = _speculative_preload(session, user_input)

    _is_first_turn = not session.messages

    if _is_first_turn:
        ui.llm_waiting("--- Scanning project---")
        build_initial_system(session, user_input)
        session.ensure_system()

    # Wait briefly for preloaded data --- max 200 ms; don't block if still loading.
    _preload_done.wait(timeout=0.2)
    _sr = getattr(session, "_speculative_results", {})
    if _sr.get("experience") and _is_first_turn:
        try:
            _exp_text = str(_sr["experience"])[:800]
            session.append({"role": "system",
                            "content": f"[Memory: similar past tasks]\n{_exp_text}"})
        except Exception:
            pass
    if _sr.get("files") and _is_first_turn:
        try:
            _file_list = "\n".join(_sr["files"])
            session.append({"role": "system",
                            "content": f"[Preloaded relevant files]\n{_file_list}"})
        except Exception:
            pass

    # Inject graph context on first turn only --- project context is already
    # in the system prompt on subsequent turns, re-injecting every turn adds
    # 0.1---0.5s of I/O overhead with no benefit.
    if _is_first_turn:
        try:
            from agent.memory import memory
            if memory.graph.is_built():
                files = memory.graph.search(user_input, top_k=12)
                if files:
                    fresh_context = memory.graph.context_for(files)
                    session.messages[:] = [
                        m for m in session.messages
                        if not (m.get("role") == "system" and m.get("content", "").startswith("[Dynamic graph context]"))
                    ]
                    inject_msg = {"role": "system", "content": f"[Dynamic graph context]\n{fresh_context}"}
                    if session.messages and session.messages[0].get("role") == "system":
                        session.messages.insert(1, inject_msg)
                    else:
                        session.messages.insert(0, inject_msg)
        except Exception:
            pass

    

    plan_dict: dict | None = None
    plan_prose: str = ""

    # Conditionally inject heavy prompt sections — saves 700-1100 tok per call
    # by only sending rules when actually relevant to this task.
    if _is_first_turn:
        try:
            from agent.prompts import BROWSER_RULES, STREAMING_RULES
            session.append({"role": "system", "content": BROWSER_RULES})
            session.append({"role": "system", "content": STREAMING_RULES})
        except Exception:
            pass

    if extra_system:
        session.append({"role": "system", "content": extra_system})
    # Skills injection removed — was adding ~900 tok per call with no measurable
    # quality benefit for direct single-agent mode. Skills are available on-demand
    # via /skills command or explicit extra_system from slash-command handlers.

    # All paths — single-agent tool loop.
    # No planner, no DAG, no mission gate, no complexity routing.

    if session.mode == "plan":
        ui.plan_mode_active()

    user_content = user_input
    if plan_dict:
        user_content = f"{user_input}\n\n[plan]\n{_plan_hint_for_model(plan_dict)}"
    elif plan_prose:
        user_content = f"{user_input}\n\n[BROWSER AUTOMATION DIRECTIVE] {plan_prose}"

    # Fast-path directive: injected for ALWAYS_SINGLE runs so the LLM knows to
    # skip any internal deliberation and dispatch tools on the very first turn.
    if getattr(session, "multi_agent_mode", "ASK") == "ALWAYS_SINGLE":
        user_content += (
            "\n\n[SPEED DIRECTIVE] Fast-path active. "
            "Dispatch tool calls on this turn --- do not emit text first. "
            "Write all required files immediately."
        )

    session.append({"role": "user", "content": user_content})

    result = run_inner_loop(
        session, MAX_TOOL_TURNS, verbose_usage=True,
    )

    # --- Experience Engine: record task outcome ---
    try:
        from agent.experience import get_experience
        _exp2 = get_experience(".")
        _exp2.learn(
            "task",
            title=user_input[:80],
            summary=result.content[:200] if result.content else "",
            tags=["medium", "coding"],
            success=(result.status == "done"),
            importance=0.5,
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
        ui.publish_turn_summary(
            status=result.status, turns_used=result.turns_used,
            tokens_in=session.cumulative_in_tokens,
            tokens_out=session.cumulative_out_tokens,
        )
        ui.turn_interrupted()
    elif result.status == "api_error":
        return result
    elif result.status == "max_turns":
        ui.publish_turn_summary(
            status=result.status, turns_used=result.turns_used,
            tokens_in=session.cumulative_in_tokens,
            tokens_out=session.cumulative_out_tokens,
        )
        ui.turn_max_reached()
    else:
        run_hooks("Stop", "", {})
        ui.publish_turn_summary(
            status=result.status, turns_used=result.turns_used,
            tokens_in=session.cumulative_in_tokens,
            tokens_out=session.cumulative_out_tokens,
        )
        ui.turn_end(
            tokens_in=session.cumulative_in_tokens,
            tokens_out=session.cumulative_out_tokens,
        )
    return result
