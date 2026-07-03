"""Agent loop: orchestration of the tool-use cycle.

This module is the brain: it asks the model what to do, dispatches the
tools the model asks for, captures the results, and repeats. Every byte
of user-facing output is emitted as an event on ``agent.ui`` --- this
module imports rich nowhere, prints nothing directly, and could be
swapped to a different UI by replacing the subscriber list.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from agent import ui

_logger = logging.getLogger(__name__)
from agent.context import build_project_map
# from agent.ecosystem.router import route  # removed: skills auto-injection disabled
from agent.env import getenv
from agent.hooks import HOOK_BLOCK_PREFIX, run_hooks
from agent.llm import ask_llm_stream, _sanitize_tool_name
from agent.permissions import ask_user, check_permission
from agent.project_context import git_status_snapshot, load_context_file
from agent.prompts import SYSTEM_PROMPT
from agent.session import get_session
# Runtime scratchpad - execution state tracking
from agent.runtime.scratchpad import ScratchpadManager, scratchpad_reset, scratch as _scratch
# from agent.skills import auto_select_skills, compose_skills # removed: skills auto-injection disabled

from agent.tools import (
    READ_ONLY_TOOLS,
    RUN_COMMAND_ERROR_MARKER,
    SELF_RENDERED_TOOLS,
    TOOL_SPECS,
    TOOLS,
)
from agent.tools._results import err, is_error

# Orchestration removed — single-agent sequential execution only.

import os as _path_os


def _safe_resolve_path(user_path: str) -> str:
    """Resolve a user/model-supplied path and verify it's within CWD.
    
    Prevents path traversal attacks (``read_file("../../../etc/passwd")``).
    Returns the resolved absolute path, or raises ValueError if outside CWD.
    """
    resolved = _path_os.path.realpath(user_path)
    cwd = _path_os.path.realpath(".")
    try:
        _path_os.path.commonpath([resolved, cwd])
    except ValueError:
        # Different drives on Windows — can't compute common path
        if resolved.startswith(cwd.rstrip("\\") + "\\") or resolved == cwd.rstrip("\\"):
            return resolved
        raise ValueError(f"Path {user_path} resolves outside working directory")
    if not resolved.startswith(cwd):
        # On different Windows drives — blocked
        if ":" in cwd and ":" in resolved and cwd.split(":")[0] != resolved.split(":")[0]:
            raise ValueError(f"Path {user_path} is on a different drive than CWD")
    return resolved


# Effectively unlimited - set to 100000 by default (can be overridden via env var)
MAX_TOOL_TURNS = int(getenv("KRYTH_MAX_TOOL_TURNS", "100000"))

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

# Tool categories for timing tracking (moved from anti_paralysis.py)
_ANALYSIS_TOOLS = {
    "read_file", "glob", "grep", "search_code", "semantic_search",
    "fts_search", "ast_search", "search_smart",
}
_IMPL_TOOLS = {
    "write_file", "edit_file", "multi_edit", "run_command",
    "run_tests", "run_install",
}



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


# Regex patterns that match file-reading shell commands which can be
# serviced from the read cache instead of hitting disk.
_READ_CMD_PATTERNS: list[tuple[re.Pattern, int]] = [
    # cat/type/Get-Content  <path>   (capture group 1 = path)
    (re.compile(r'^(?:cat|type|Get-Content|gc)\s+["\']?(.+?)["\']?\s*$', re.I), 1),
    # head/tail  -n  <path>   or  head/tail  <path>
    (re.compile(r'^(?:head|tail)(?:\s+-\d+)?\s+["\']?(.+?)["\']?\s*$', re.I), 1),
    # python -c "with open(...) as f: print(f.read())"  — extract path
    (re.compile(r"""["']with\s+open\(["']([^"']+)["']""", re.I), 1),
]


def _try_get_read_from_cmd(cmd: str, session) -> Optional[str]:
    """If *cmd* is a file-reading shell command, try to serve its target
    from the read cache.  Returns a pre-formatted result string, or None."""
    _path: Optional[str] = None
    for pat, grp in _READ_CMD_PATTERNS:
        m = pat.search(cmd.strip())
        if m:
            _path = m.group(grp).strip().strip("\"'").lstrip(".").lstrip("/").lstrip("\\")
            break
    if not _path:
        return None
    try:
        _dup = session.memory_manager.check_duplicate_read(_path)
        if not _dup:
            return None
        _content = _dup.get("summary", "")
        try:
            from agent.memory import get_cached_read as _gcr
            _cached = _gcr(id(session), _path)
            if _cached and _cached.result:
                _content = _cached.result
        except Exception as _e:
            _logger.debug("_try_get_read_from_cmd get_cached_read: %s", _e)
        return f"{_content}\n\n[— end of file (from cache) —]"
    except Exception as _e:
        _logger.debug("_try_get_read_from_cmd failed: %s", _e)
        return None


def _fix_windows_command(cmd: str) -> str:
    """Rewrite Windows-incompatible command patterns before execution.
    Returns the (possibly modified) command string.
    """
    if not __import__("os").name == "nt":
        return cmd
    # python3 → python (Windows has no python3 symlink)
    cmd = re.sub(r'\bpython3\b', 'python', cmd)
    # pip3 → pip
    cmd = re.sub(r'\bpip3\b', 'pip', cmd)
    # grep → findstr (when inside a pipe chain on Windows)
    cmd = re.sub(r'\|\s*grep\b', '| findstr', cmd)
    return cmd


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

    # ── Path traversal guard ─────────────────────────────────────────────
    # Resolve all path arguments to prevent "../../../etc/passwd" attacks.
    # This runs BEFORE any tool executes.
    if tool_name in ("read_file", "write_file", "edit_file", "multi_edit", "delete_file"):
        _path_arg = args.get("path", "")
        if _path_arg:
            try:
                args["path"] = _safe_resolve_path(_path_arg)
            except (ValueError, OSError) as _pe:
                _logger.warning("dispatch_tool_call path traversal blocked: %s -> %s", tool_name, _path_arg)
                result = err(
                    "PATH_TRAVERSAL",
                    f"Path '{_path_arg}' resolves outside the working directory and was blocked.",
                )
                ui.tool_start(tool_name, args)
                ui.tool_error(f"path traversal blocked: {_path_arg}")
                _append_tool_msg(session, call_id, tool_name, result)
                return

    # ── Memory pre-check: duplicate read / command detection ──────────────
    if tool_name == "read_file":
        _path = args.get("path", "")
        if _path:
            try:
                _dup = session.memory_manager.check_duplicate_read(_path)
                if _dup:
                    ui.debug(f"(memory: {_path} already read, injecting cached content)")
                    _content = _dup.get("summary", "")
                    try:
                        from agent.memory import get_cached_read as _gcr
                        _cached = _gcr(id(session), _path)
                        if _cached and _cached.result:
                            _content = _cached.result
                    except Exception as _e:
                        _logger.debug("dispatch_tool_call get_cached_read: %s", _e)
                    result = f"{_content}\n\n[— end of file (from cache) —]"
                    ui.tool_start(tool_name, args)
                    ui.tool_result(result, error=False)
                    _append_tool_msg(session, call_id, tool_name, result)
                    return
            except Exception as _e:
                _logger.debug("dispatch_tool_call check_duplicate_read: %s", _e)
    elif tool_name == "run_command":
        _cmd = args.get("command", "")
        _cwd = args.get("cwd", "")
        if _cmd:
            try:
                _dup = session.memory_manager.check_duplicate_command(_cmd, _cwd)
                if _dup:
                    _prev_output = _dup.get("previous_output", "")
                    _exit = _dup.get("exit_code", 0)
                    _run_count = _dup.get("run_count", 1)
                    ui.debug(f"(memory: command '{_cmd[:40]}' run {_run_count}x before)")
                    result = (
                        f"[FROM EXECUTION MEMORY — previously run {_run_count}x, "
                        f"exit={_exit}]\n{_prev_output}"
                    )
                    ui.tool_start(tool_name, args)
                    ui.tool_result(result, error=_exit != 0)
                    _append_tool_msg(session, call_id, tool_name, result)
                    return
            except Exception as _e:
                _logger.debug("dispatch_tool_call check_duplicate_command: %s", _e)
        if _cmd:
            try:
                _cached = _try_get_read_from_cmd(_cmd, session)
                if _cached:
                    ui.debug(f"(memory: '{_cmd[:40]}' is a file-read command, serving from cache)")
                    result = _cached
                    ui.tool_start(tool_name, args)
                    ui.tool_result(result, error=False)
                    _append_tool_msg(session, call_id, tool_name, result)
                    return
            except Exception as _e:
                _logger.debug("dispatch_tool_call _try_get_read_from_cmd: %s", _e)
        if _cmd:
            _fixed = _fix_windows_command(_cmd)
            if _fixed != _cmd:
                ui.debug(f"(memory: fixed Windows command: {_fixed[:80]})")
                args["command"] = _fixed
                _cmd = _fixed
    elif tool_name == "edit_file":
        _path = args.get("path", "")
        _old = args.get("old_string", "") or args.get("old_text", "")
        _new = args.get("new_string", "") or args.get("new_text", "")
        if _path and _old:
            try:
                _dup = session.memory_manager.check_duplicate_edit(_path, _old, _new)
                if _dup:
                    ui.debug(f"(memory: edit already applied to {_path})")
                    result = f"[FROM MUTATION MEMORY — edit already applied to {_path}]\n{_dup['summary']}"
                    ui.tool_start(tool_name, args)
                    ui.tool_result(result, error=False)
                    _append_tool_msg(session, call_id, tool_name, result)
                    return
            except Exception as _e:
                _logger.debug("dispatch_tool_call check_duplicate_edit: %s", _e)

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
            "def ", "class ", "return ", "print(",
            "if __name", "raise ",
        )
        _shell_prefix_indicators = ("from ",)
        _has_nonsense = any(kw in _cmd for kw in _nonsense_indicators)
        _has_shell_prefix = (
            any(kw in _cmd for kw in _shell_prefix_indicators)
            and "\n" in _cmd
        )
        _has_non_ascii = bool(re.search(r"[^\x00-\x7F\s]", _cmd[:40]))
        if _has_nonsense or _has_shell_prefix or _has_non_ascii:
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
    except Exception as _e:
        _logger.debug("dispatch_tool_call mission_control: %s", _e)

    _tool_start_ts = __import__("time").monotonic()
    result = execute_tool(tool_name, args)
    _tool_elapsed = __import__("time").monotonic() - _tool_start_ts
    session.tool_call_count += 1
    _clear_denials(session, tool_name)

    # Record timing on session (replaces anti_paralysis.py)
    if _tool_elapsed > 0:
        if tool_name in _ANALYSIS_TOOLS:
            session.analysis_time_s += _tool_elapsed
        elif tool_name in _IMPL_TOOLS:
            session.impl_time_s += _tool_elapsed

    # ── Memory recording: persist tool results ────────────────────────────
    _error = has_error(result)
    try:
        session.memory_manager.on_tool_result(tool_name, args, result, error=_error)
    except Exception as _e:
        _logger.debug("dispatch_tool_call on_tool_result: %s", _e)
    # Special case: record read_file in ReadMemory for duplicate detection
    if tool_name == "read_file" and not _error:
        _path = args.get("path", "")
        if _path:
            try:
                from agent.memory import record_read_file as _rrf
                _rrf(id(session), _path, args, str(result))
            except Exception as _e:
                _logger.debug("dispatch_tool_call record_read_file: %s", _e)

    # Scratchpad: completion tracking is handled by update_after_tool

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
                except Exception as _e:
                    _logger.debug("dispatch_tool_call bg_validate: %s", _e)
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
    except Exception as _e:
        _logger.debug("dispatch_tool_call dashboard: %s", _e)

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
    except Exception as _e:
        _logger.debug("dispatch_tool_call compress_result: %s", _e)
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
    except Exception as _e:
        _logger.debug("dispatch_tool_call output_summarizer: %s", _e)

    # Auto-compress browser results every N tool calls
        try:
            from agent.context_manager import compress_messages, COMPRESS_EVERY_N
            if session.tool_call_count % COMPRESS_EVERY_N == 0:
                with session._lock:
                    session.messages, dropped = compress_messages(session.messages)
                if dropped > 0:
                    ui.debug(f"(context: compressed {dropped:,} chars of old browser results)")
        except Exception as _e:
            _logger.debug("dispatch_tool_call compress_messages: %s", _e)

    # Periodic history checkpointing — compresses old turns into structured JSON
        try:
            from agent.checkpoint_manager import should_checkpoint, apply_checkpoint
            with session._lock:
                _ckpt_count = getattr(session, "_tool_calls_since_checkpoint", 0) + 1
                session._tool_calls_since_checkpoint = _ckpt_count
                if should_checkpoint(session, _ckpt_count):
                    session.messages, _freed = apply_checkpoint(session.messages)
                    session._tool_calls_since_checkpoint = 0
                    if _freed > 0:
                        ui.debug(f"(checkpoint: archived {_freed:,} chars of old history)")
        except Exception as _e:
            _logger.debug("dispatch_tool_call checkpoint: %s", _e)

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
    session._speculative_results = None

    lower = user_input.lower()
    patterns: list[str] = []
    for keyword, pats in _KEYWORD_PRELOAD_MAP.items():
        if keyword in lower:
            patterns.extend(pats)
    # dedupe preserving first-seen order
    seen: set[str] = set()
    patterns = [p for p in patterns if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]

    pending = [1 if patterns else 0]  # just _preload_files if patterns match
    lock = _t.Lock()

    def _tick() -> None:
        with lock:
            pending[0] -= 1
            if pending[0] == 0:
                done.set()

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
                        except OSError as _e:
                            _logger.debug("_speculative_preload getsize: %s", _e)
                if len(found) >= 12:
                    break
            if found:
                with session._lock:
                    session._speculative_results = {"files": found[:12]}
        except Exception as _e:
            _logger.debug("_speculative_preload failed: %s", _e)
        finally:
            _tick()

    if patterns:
        _t.Thread(target=_preload_files, daemon=True, name="kryth-file-preload").start()
    else:
        done.set()

    return done


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
            except Exception as _e:
                _logger.debug("_expand_run_command_paths write_file parse: %s", _e)
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
        except Exception as _e:
            _logger.debug("_expand_run_command_paths run_command rewrite: %s", _e)


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
    # Rule 23: performance counters
    _loop_start = _time.monotonic()
    _total_planning_s: float = 0.0
    _total_executing_s: float = 0.0    # Context supervisor—tiered budget-aware compression. Always available.
    from agent.context_supervisor import ContextSupervisor
    _supervisor = ContextSupervisor(session)

    for _ in range(max_turns):
        turn_count += 1
        # Rule 22: synchronous context supervision
        _supervisor.check()
        _enforce_message_order(session)

        # Emit live progress for subagents so the UI shows current state.
        if _is_subagent and turn_count > 1:
            role = getattr(session, "_agent_role", "")
            label = f"--- {role} - turn {turn_count}/{max_turns}" if role else f"--- turn {turn_count}/{max_turns}"
            ui.llm_waiting(label)

        # ── Tool curation ────────────────────────────────────────────────
        # Single authority: scratchpad.curate_tools() filters by intent + domains.
        # If a prior turn escalated, we use the full set for the rest of the session.
        _turn_tools = TOOL_SPECS
        if not getattr(session, "_tools_full_escalated", False):
            try:
                _turn_tools = _scratch.curate_tools(TOOL_SPECS)
            except Exception as _e:
                _logger.warning("scratchpad.curate_tools failed, using full set: %s", _e)
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
            except Exception as _e:
                _logger.debug("run_inner_loop token_budget: %s", _e)

        # Hook: inject scratchpad prompt block before LLM call
        _scratch_state = None
        _mem_state = None
        try:
            _block = _scratch.render_prompt_block()
            if _block:
                with session._lock:
                    _scratch_state = len(session.messages)
                    session.messages.append({"role": "system", "content": _block})
        except Exception as _e:
            _logger.warning("scratchpad.render_prompt_block failed: %s", _e)

        # Inject memory context on every turn (known files, functions, commands)
        try:
            from agent.memory import get_context_summary as _gcs
            _mem_block = _gcs(id(session), max_tokens=400)
            if _mem_block:
                with session._lock:
                    _mem_state = len(session.messages)
                    session.messages.append({"role": "system", "content": _mem_block})
        except Exception as _e:
            _logger.debug("run_inner_loop get_context_summary: %s", _e)

        _turn_llm_start = _time.monotonic()
        response = ask_llm_stream(
            session.messages,
            tools=_turn_tools,
        )

        # Remove injected scratchpad and memory blocks (in reverse order)
        if _mem_state is not None or _scratch_state is not None:
            with session._lock:
                if _mem_state is not None:
                    try:
                        session.messages.pop(_mem_state)
                    except Exception as _e:
                        _logger.debug("run_inner_loop pop mem_state: %s", _e)
                if _scratch_state is not None:
                    try:
                        session.messages.pop(_scratch_state)
                    except Exception as _e:
                        _logger.warning("failed to pop scratchpad block: %s", _e)
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
                _supervisor._emergency_archive(None)
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
            except Exception as _e:
                _logger.debug("run_inner_loop max_tokens_cache update: %s", _e)
            if session.messages and session.messages[-1].get("role") == "assistant":
                session.messages.pop()
            turn_count -= 1
            continue

        tool_calls = response["tool_calls"] or []

        # Intent from scratchpad — single authority. Used by question guard below.
        _current_intent = (_scratch.state.intent if _scratch.state else "READ")

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

            # ── Scratchpad completion nudge ──────────────────────────────
            try:
                _nudge = _scratch.next_nudge()
                if _nudge:
                    session.append({"role": "user", "content": _nudge})
                    turn_count -= 1
                    continue
            except Exception as _e:
                _logger.warning("scratchpad.next_nudge failed: %s", _e)

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

            # ── Single evaluate() — the ONE completion authority ──────────
            # All text-only responses go through scratchpad.evaluate().
            # It decides: done (return), nudge (continue), or fallthrough.
            # This replaces the old "if total_tool_calls == 0 → done" path,
            # which bypassed scratchpad and caused premature completion.
            _has_blocked = any(
                m.get("role") == "tool" and m.get("name") == "run_command"
                and (m.get("content") or "").startswith("[COMMAND BLOCKED")
                for m in session.messages[-12:]
            )
            try:
                _decision = _scratch.evaluate(
                    has_blocked_commands=_has_blocked,
                    is_question_turn=_current_intent in ("CHAT", "CHAT_READ", "READ", "SEARCH"),
                )
                if _decision.done:
                    return LoopResult(status="done", content=content or "", turns_used=turn_count, finish_reason=_decision.finish_reason)
                if _decision.nudge:
                    session.append({"role": "user", "content": _decision.nudge})
                    continue
            except Exception as _e:
                _logger.warning("scratchpad.evaluate failed: %s", _e)
            # Fallthrough: scratchpad had no opinion — treat as done.
            return LoopResult(status="done", content=content or "", turns_used=turn_count, finish_reason="completed")

        # Tools were called --- reset the no-tool counter.
        _consecutive_no_tool_turns = 0
        _total_tool_calls += len(tool_calls)

        # Detect infinite tool loops
        hermes_recovered = response.get("finish_reason") in (
            "recovered_after_stream_error", None
        ) and any(
            (tc.get("id") or "").startswith("hermes_") for tc in tool_calls
        )
        # Question-intent guard: if the user's message is a question
        # and there are already file read results in context, block execution
        # tools (write_file, run_command, edit_file) so the model answers
        # from context. Prevents "what is incomplete?" → pip install → python.
        if _current_intent in ("CHAT", "CHAT_READ", "READ", "SEARCH"):
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

        # Hook: update scratchpad after tool execution
        try:
            for _call in tool_calls:
                _tname = (_call.get("function") or {}).get("name", "")
                _raw_args = (_call.get("function") or {}).get("arguments", "{}")
                _parsed = {}
                try:
                    _parsed = json.loads(_raw_args) if _raw_args else {}
                except Exception as _e2:
                    _logger.warning("failed to parse tool args for scratchpad: %s", _e2)
                _last_tool = next(
                    (m for m in reversed(session.messages) if m.get("role") == "tool"),
                    None,
                )
                _result = _last_tool.get("content", "") if _last_tool else ""
                _scratch.update_after_tool(_tname, _result, args=_parsed)
        except Exception as _e:
            _logger.warning("scratchpad.update_after_tool failed: %s", _e)

        # ── Post-tool nudge: if scratchpad says enough info, nudge to summarize ──
        # Don't return early (that would skip the final response). Instead inject
        # a nudge so the model produces a summary, which evaluate() catches.
        try:
            if _scratch.should_finish():
                _tip = "[scratchpad] SUMMARIZE: Task complete — provide a concise summary of what you found."
                session.append({"role": "user", "content": _tip})
                continue
        except Exception as _e:
            _logger.warning("scratchpad.should_finish failed: %s", _e)

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
        except Exception as _e:
            _logger.debug("run_inner_loop dashboard perf: %s", _e)

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
    ui.muted(
        f"  Duplicate searches: {session.duplicate_searches}  |  "
        f"Analysis: {session.analysis_time_s:.1f}s  |  "
        f"Execution: {session.impl_time_s:.1f}s"
    )
    return LoopResult(status="max_turns", turns_used=turn_count, finish_reason="max_turns")


# ---------------------------------------------------------------------------
# Planning heuristic
# ---------------------------------------------------------------------------
def build_initial_system(session, user_input: str = ""):
    from agent.context import ProjectSnapshot, build_focused_map, build_retrieval_context
    import os as _os

    _cwd = _os.getcwd()
    project_map = ""
    project_doc = ""
    git_state = ""

    # --- I/O: project map, context doc, git state ---
    import concurrent.futures as _cf_init

    def _get_project_map():
        # Priority 1: grep-first retrieval (Claude-Code style)
        try:
            _retrieval = build_retrieval_context(user_input)
            if _retrieval:
                ui.debug(f"(retrieval context: grep-based)")
                return _retrieval
        except Exception as _e:
            _logger.debug("retrieval context failed: %s", _e)

        # Priority 2: mtime-cached focused directory map (fallback)
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

    # Inject memory context — known files, functions, classes already read
    try:
        _mem_block = session.memory_manager.get_prompt_block(user_input=user_input)
        if _mem_block:
            parts.append(_mem_block)
    except Exception as _e:
        _logger.warning("build_initial_system get_prompt_block: %s", _e)
    # Inject ReadMemory context — cached file summaries
    try:
        from agent.memory import get_context_summary
        _read_mem = get_context_summary(id(session), max_tokens=600)
        if _read_mem:
            parts.append(_read_mem)
    except Exception as _e:
        _logger.warning("build_initial_system get_context_summary: %s", _e)

    session.system_prompt = "\n\n".join(parts)

    # Token breakdown for debugging
    ui.debug(
        f"  ctx: prompt={len(_sys_prompt)//4} tok"
        f"  mem={len(project_doc)//4} tok"
        f"  git={len(git_state)//4} tok"
        f"  map={len(project_map)//4} tok"
    )

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
        with session._lock:
            session.messages = [
                m for m in session.messages if m.get("role") == "system"
            ]
        ui.muted("  (previous task interrupted — starting fresh)")


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
    with session._lock:
        _sr = session._speculative_results or {}
    if _sr.get("experience") and _is_first_turn:
        try:
            _exp_text = str(_sr["experience"])[:800]
            session.append({"role": "system",
                            "content": f"[Memory: similar past tasks]\n{_exp_text}"})
        except Exception as _e:
            _logger.debug("run_agent speculative experience: %s", _e)
    if _sr.get("files") and _is_first_turn:
        try:
            _file_list = "\n".join(_sr["files"])
            session.append({"role": "system",
                            "content": f"[Preloaded relevant files]\n{_file_list}"})
        except Exception:
            pass    # Conditionally inject heavy prompt sections — saves 700-1100 tok per call
    # by only sending rules when actually relevant to this task.
    if _is_first_turn:
        try:
            from agent.prompts import BROWSER_RULES, STREAMING_RULES
            session.append({"role": "system", "content": BROWSER_RULES})
            session.append({"role": "system", "content": STREAMING_RULES})
        except Exception as _e:
            _logger.warning("failed to inject rule prompts: %s", _e)

    if extra_system:
        session.append({"role": "system", "content": extra_system})

    if session.mode == "plan":
        ui.plan_mode_active()

    user_content = user_input

    # Fast-path directive: injected for ALWAYS_SINGLE runs so the LLM knows to
    # skip any internal deliberation and dispatch tools on the very first turn.
    if getattr(session, "multi_agent_mode", "ASK") == "ALWAYS_SINGLE":
        user_content += (
            "\n\n[SPEED DIRECTIVE] Fast-path active. "
            "Dispatch tool calls on this turn --- do not emit text first. "
            "Write all required files immediately."
        )

    session.append({"role": "user", "content": user_content})

    # Hook: init scratchpad for this task
    try:
        _scratch.initialize(user_content)
    except Exception as _e:
        _logger.warning("scratchpad.initialize failed: %s", _e)

    result = run_inner_loop(
        session, MAX_TOOL_TURNS, verbose_usage=True,
    )

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
    except Exception as _e:
        _logger.warning("session persistence failed: %s", _e)

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
