"""Public UI surface.

The rest of the codebase imports from ``agent.ui`` only. Internal
modules (renderer, components, diff_renderer, panels, updates,
syntax, streaming) are implementation detail and should not be
touched directly.

Architecture:
    emitter (agent_loop, llm, tools, ...)
        │
        ▼
    BUS  (agent.ui.events)
        │
        ▼
    renderer  (agent.ui.renderer)
        │
        ▼
    Rich Console  (agent.ui.console)

The renderer is installed once at REPL startup. Emitters publish
``Event`` objects; the renderer transforms them into Rich output.

Two convenience surfaces are exposed:
- ``emit(kind, **data)`` — low-level, for new event types.
- Named helpers (``banner``, ``tool_start``, ``diff``, ...) — sugar over
  ``emit`` so call sites read like prose.
"""

from __future__ import annotations

from typing import Any, Iterable

from agent.ui.events import BUS, Event, EventKind, emit
from agent.ui.logger import get_logger
from agent.ui import renderer as _renderer
from agent.ui import run_summary as _summary


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def install() -> None:
    """Install the default Rich renderer + run-summary tracker on the
    shared bus. Call once from the REPL entrypoint. Safe to call
    multiple times.
    """
    _renderer.install()
    _summary.install()


def uninstall() -> None:
    _renderer.uninstall()


def begin_turn() -> None:
    """Reset per-turn trackers. Called by the agent loop at turn start."""
    _summary.reset()


def publish_turn_summary(*, status: str = "done", turns_used: int = 0) -> None:
    """If side-effects happened OR the turn ended abnormally, emit the
    RUN_SUMMARY panel. ``status`` is one of ``done``/``max_turns``/
    ``interrupted``/``api_error`` and is passed through to the renderer
    so it can paint the appropriate header."""
    summary = _summary.snapshot()
    summary["status"] = status
    summary["turns_used"] = turns_used
    if status == "done" and _summary.is_empty():
        return
    emit(EventKind.RUN_SUMMARY, summary=summary)


# ---------------------------------------------------------------------------
# Named emitters — call sites stay readable.
# ---------------------------------------------------------------------------

# Lifecycle ----------------------------------------------------------

def banner(model: str, base_url: str, skill_count: int = 0) -> None:
    emit(EventKind.BANNER, model=model, base_url=base_url, skill_count=skill_count)


def status(model: str, mode: str, tokens_in: int, tokens_out: int) -> None:
    emit(EventKind.STATUS, model=model, mode=mode,
         tokens_in=tokens_in, tokens_out=tokens_out)


def turn_start() -> None:
    emit(EventKind.TURN_START)


def turn_end(tokens_in: int = 0, tokens_out: int = 0) -> None:
    emit(EventKind.TURN_END, tokens_in=tokens_in, tokens_out=tokens_out)


def turn_interrupted() -> None:
    emit(EventKind.TURN_INTERRUPTED)


def turn_max_reached() -> None:
    emit(EventKind.TURN_MAX_TURNS)


def session_reset() -> None:
    emit(EventKind.SESSION_RESET)


# Planning ----------------------------------------------------------

def plan(plan_dict: dict) -> None:
    emit(EventKind.PLAN, plan=plan_dict)


def plan_prose(text: str) -> None:
    emit(EventKind.PLAN_PROSE, text=text)


def plan_mode_active() -> None:
    emit(EventKind.PLAN_MODE)


def auto_skills(names: Iterable[str]) -> None:
    emit(EventKind.AUTO_SKILLS, skills=list(names))


# LLM stream --------------------------------------------------------

def llm_waiting(message: str = "waiting for model…") -> None:
    emit(EventKind.LLM_WAITING, message=message)


def llm_reasoning_chunk(piece: str, elapsed: float = 0.0) -> None:
    emit(EventKind.LLM_REASONING_CHUNK, piece=piece, elapsed=elapsed)


def llm_reasoning_end() -> None:
    emit(EventKind.LLM_REASONING_END)


def llm_content_chunk(piece: str) -> None:
    emit(EventKind.LLM_CONTENT_CHUNK, piece=piece)


def llm_content_end(*, render_markdown: bool = True) -> None:
    emit(EventKind.LLM_CONTENT_END, render_markdown=render_markdown)


def llm_usage(turn_in: int, turn_out: int, session_in: int, session_out: int, verbose: bool = True) -> None:
    emit(EventKind.LLM_USAGE,
         turn_in=turn_in, turn_out=turn_out,
         session_in=session_in, session_out=session_out,
         verbose=verbose)


def llm_error(label: str, message: str, hint: str | None = None) -> None:
    emit(EventKind.LLM_ERROR, label=label, message=message, hint=hint)


def llm_retry(label: str, attempt: int, total: int, reason: str,
              *, delay: float | None = None) -> None:
    emit(EventKind.LLM_RETRY,
         label=label, attempt=attempt, total=total, reason=reason,
         delay=delay)


def llm_hermes_recovery(count: int) -> None:
    emit(EventKind.LLM_HERMES_RECOVERY, count=count)


def llm_degenerate() -> None:
    emit(EventKind.LLM_DEGENERATE)


# Tool dispatch -----------------------------------------------------

def tool_start(name: str, args: dict | None = None) -> None:
    emit(EventKind.TOOL_START, name=name, args=args or {})


def tool_result(result: str, *, error: bool = False) -> None:
    emit(EventKind.TOOL_RESULT, result=result, error=error)


def tool_error(message: str) -> None:
    emit(EventKind.TOOL_ERROR, message=message)


def tool_cancelled() -> None:
    emit(EventKind.TOOL_CANCELLED)


def tool_coerced(tool: str, key: str, arg_type: str, count: int) -> None:
    emit(EventKind.TOOL_COERCED, tool=tool, key=key, arg_type=arg_type, count=count)


def tool_denied(tool: str) -> None:
    emit(EventKind.TOOL_DENIED, tool=tool)


def tool_hook_blocked(message: str) -> None:
    emit(EventKind.TOOL_HOOK_BLOCKED, message=message)


def permission_request(tool: str, signature: str) -> str:
    """Show the interactive permission prompt and return the user's
    choice as ``'y'``, ``'a'``, or ``'n'``.

    Bypasses the event bus on purpose: this is a synchronous,
    blocking UI interaction, not a stream event.
    """
    from agent.ui.components import ask_permission_interactive
    return ask_permission_interactive(tool, signature)


# File / shell ------------------------------------------------------

def write_preview(path: str, content: str) -> None:
    emit(EventKind.WRITE_PREVIEW, path=path, content=content)


def diff(diff_text: str, *, path: str | None = None, title: str | None = None) -> None:
    emit(EventKind.DIFF, diff=diff_text, path=path, title=title)


def shell_run(command: str, timeout: int, note: str | None = None) -> None:
    emit(EventKind.SHELL_RUN, command=command, timeout=timeout, note=note)


def shell_end(
    *,
    command: str,
    output: str,
    exit_code: int,
    timeout: int,
    note: str | None = None,
) -> None:
    """Close a shell run with the full output. The renderer composes
    a single command panel from this event."""
    emit(
        EventKind.SHELL_END,
        command=command,
        output=output,
        exit_code=exit_code,
        timeout=timeout,
        note=note,
    )


def run_summary(summary: dict) -> None:
    """End-of-turn summary of side-effects accumulated during the run."""
    emit(EventKind.RUN_SUMMARY, summary=summary)


def todos(items: list[dict]) -> None:
    emit(EventKind.TODOS, items=items)


# Subagent ----------------------------------------------------------

def subagent_start(depth: int, description: str) -> None:
    emit(EventKind.SUBAGENT_START, depth=depth, description=description)


def subagent_end(depth: int) -> None:
    emit(EventKind.SUBAGENT_END, depth=depth)


# Compaction --------------------------------------------------------

def compact_start(count: int, tokens: int) -> None:
    emit(EventKind.COMPACT_START, count=count, tokens=tokens)


def compact_fallback(*, dropped_messages: int = 0,
                     dropped_chars: int = 0) -> None:
    emit(EventKind.COMPACT_FALLBACK,
         dropped_messages=dropped_messages,
         dropped_chars=dropped_chars)


# Generic log -------------------------------------------------------

def info(message: str) -> None:
    emit(EventKind.LOG, level="info", message=message)


def warn(message: str) -> None:
    emit(EventKind.LOG, level="warn", message=message)


def error(message: str) -> None:
    emit(EventKind.LOG, level="error", message=message)


def success(message: str) -> None:
    emit(EventKind.LOG, level="success", message=message)


def debug(message: str) -> None:
    emit(EventKind.LOG, level="debug", message=message)


def muted(message: str) -> None:
    """Subtle one-liner for user-facing notices (commands, status)."""
    from agent.ui.console import console
    console.print(f"[muted]{message}[/muted]")


# ---------------------------------------------------------------------------
# Next-Gen UI emitters
# ---------------------------------------------------------------------------

def mission_start(goal: str, estimated_duration: str = "") -> None:
    emit(EventKind.MISSION_START, goal=goal, estimated_duration=estimated_duration)


def mission_progress(percent: int, stage: str = "") -> None:
    emit(EventKind.MISSION_PROGRESS, percent=percent, stage=stage)


def mission_complete(summary: dict | None = None) -> None:
    emit(EventKind.MISSION_COMPLETE, summary=summary or {})


def mission_failed(reason: str = "") -> None:
    emit(EventKind.MISSION_FAILED, reason=reason)


def agent_update(name: str, status: str, task: str = "", progress: int = 0) -> None:
    """Update a named agent's status in the multi-agent dashboard."""
    emit(EventKind.AGENT_UPDATE, name=name, status=status, task=task, progress=progress)


def agent_created(agent_id: str, role: str, task_count: int = 0) -> None:
    emit(EventKind.AGENT_CREATED, agent_id=agent_id, role=role, task_count=task_count)
    agent_update(name=role, status="created", task=f"{task_count} task(s)")


def agent_task_start(agent_id: str, task_id: str, description: str = "") -> None:
    emit(EventKind.AGENT_TASK_START, agent_id=agent_id, task_id=task_id, description=description)
    agent_update(name=agent_id, status="running", task=description[:60])


def agent_task_done(agent_id: str, task_id: str, turns_used: int = 0) -> None:
    emit(EventKind.AGENT_TASK_DONE, agent_id=agent_id, task_id=task_id, turns_used=turns_used)
    agent_update(name=agent_id, status="done", progress=100)


def agent_failed(agent_id: str, role: str, reason: str = "") -> None:
    emit(EventKind.AGENT_FAILED, agent_id=agent_id, role=role, reason=reason)
    agent_update(name=role, status="failed", task=reason[:60])


def work_stolen(agent_id: str, task_id: str) -> None:
    emit(EventKind.WORK_STOLEN, agent_id=agent_id, task_id=task_id)
    muted(f"  ◈ {agent_id} stole task {task_id}")


def queue_status(pending: int, running: int, done: int, failed: int) -> None:
    emit(EventKind.QUEUE_STATUS, pending=pending, running=running, done=done, failed=failed)


def stop_spinner() -> None:
    """Stop the Rich Live spinner immediately.

    MUST be called before any interactive input() call (approval prompts,
    permission gates, etc.) — otherwise Rich owns the terminal cursor and
    keystrokes are swallowed by the Live renderer instead of reaching input().
    """
    try:
        from agent.ui.renderer import _activity
        _activity.idle()
    except Exception:
        pass


def timeline_event(message: str, kind: str = "info") -> None:
    """Append an event to the live activity timeline."""
    emit(EventKind.TIMELINE_EVENT, message=message, kind=kind)


def engineering_action(label: str, status: str = "running", detail: str = "") -> None:
    """Emit a high-level engineering action (hides raw tool names)."""
    emit(EventKind.ENGINEERING_ACTION, label=label, status=status, detail=detail)


def engineering_section(title: str) -> None:
    emit(EventKind.ENGINEERING_SECTION, title=title)


def layer_change(layer: str) -> None:
    """Switch the active UI layer: executive | engineering | terminal | debug."""
    emit(EventKind.LAYER_CHANGE, layer=layer)


def approval_batch(items: list, risk: str = "low", estimated_time: str = "") -> None:
    """Request batch approval for a list of planned actions."""
    emit(EventKind.APPROVAL_BATCH, items=items, risk=risk, estimated_time=estimated_time)


def reflection_show(worked: str = "", failed: str = "", learned: str = "") -> None:
    emit(EventKind.REFLECTION, worked=worked, failed=failed, learned=learned)


def memory_display(similar_tasks: int = 0, best_workflow: str = "", success_rate: int = 0) -> None:
    emit(EventKind.MEMORY_DISPLAY, similar_tasks=similar_tasks,
         best_workflow=best_workflow, success_rate=success_rate)


def terminal_summary(command: str, status: str, metrics: dict | None = None,
                     expandable_log: str = "") -> None:
    """Emit an abstracted terminal result (replaces raw log flood)."""
    emit(EventKind.TERMINAL_SUMMARY, command=command, status=status,
         metrics=metrics or {}, expandable_log=expandable_log)


def dag_update(nodes: list) -> None:
    """Update the DAG visualization with current execution graph state."""
    emit(EventKind.DAG_UPDATE, nodes=nodes)


# ---------------------------------------------------------------------------
# Re-exports
# ---------------------------------------------------------------------------

__all__ = [
    # lifecycle
    "install", "uninstall",
    "banner", "status",
    "turn_start", "turn_end", "turn_interrupted", "turn_max_reached",
    "session_reset",
    # planning
    "plan", "plan_prose", "plan_mode_active", "auto_skills",
    # llm
    "llm_waiting",
    "llm_reasoning_chunk", "llm_reasoning_end",
    "llm_content_chunk", "llm_content_end",
    "llm_usage", "llm_error", "llm_retry",
    "llm_hermes_recovery", "llm_degenerate",
    # tools
    "tool_start", "tool_result", "tool_error",
    "tool_cancelled", "tool_coerced", "tool_denied", "tool_hook_blocked",
    "permission_request",
    # file/shell
    "write_preview", "diff", "shell_run", "shell_end", "todos",
    "run_summary",
    # subagent
    "subagent_start", "subagent_end",
    # compact
    "compact_start", "compact_fallback",
    # log
    "info", "warn", "error", "success", "debug", "muted",
    # plumbing
    "BUS", "Event", "EventKind", "emit", "get_logger",
    # next-gen UI
    "mission_start", "mission_progress", "mission_complete", "mission_failed",
    "agent_update", "timeline_event",
    "engineering_action", "engineering_section",
    "layer_change", "approval_batch",
    "reflection_show", "memory_display",
    "terminal_summary", "dag_update",
]
