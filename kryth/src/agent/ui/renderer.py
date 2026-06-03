"""Event-driven terminal renderer.

Subscribes to ``BUS`` and translates ``Event``s into Rich output by
delegating to components, updates (diff/create panels), streaming.
This is the single place where event-kind dispatch lives — adding a
new event means registering a handler here.
"""

from __future__ import annotations

from typing import Callable

from rich.text import Text

from agent.ui import components as C
from agent.ui import command_panel
from agent.ui import status_manager as status
from agent.ui import updates as file_updates
from agent.ui.activity import ActivityIndicator
from agent.ui.console import console
from agent.ui.events import BUS, Event, EventKind
from agent.ui.streaming import StreamPrinter
from agent.ui.summarizer import summarize_output
from agent.ui.theme import CORE, ERROR


# Module-level state for streaming + activity indicator. One agent runs
# at a time in the REPL, so a singleton is fine; the renderer resets
# these explicitly on turn boundaries.
_activity = ActivityIndicator()
_stream = StreamPrinter()


# ---------------------------------------------------------------------------
# Helpers for tool-call rendering
# ---------------------------------------------------------------------------

def _summarize_args(name: str, args: dict) -> str:
    if not args:
        return ""
    if "path" in args and isinstance(args["path"], str):
        return args["path"]
    if "command" in args and isinstance(args["command"], str):
        cmd = args["command"]
        return cmd if len(cmd) <= 100 else cmd[:97] + "…"
    if "pattern" in args:
        return f'"{args["pattern"]}"'
    if "query" in args:
        return f'"{args["query"]}"'
    if "name" in args and isinstance(args["name"], str):
        return args["name"]
    if "items" in args and isinstance(args["items"], list):
        return f"{len(args['items'])} items"
    if "edits" in args and isinstance(args["edits"], list):
        return f"{len(args['edits'])} edits"
    try:
        first_val = next(iter(args.values()))
    except StopIteration:
        return ""
    s = str(first_val)
    return s if len(s) <= 100 else s[:97] + "…"


def _result_first_line(result: str) -> tuple[str, int]:
    text = result.strip() if isinstance(result, str) else str(result)
    if not text:
        return "(empty)", 0
    lines = text.splitlines()
    first = lines[0]
    if len(first) > 180:
        first = first[:177] + "…"
    return first, max(0, len(lines) - 1)


# ---------------------------------------------------------------------------
# Per-event handlers
# ---------------------------------------------------------------------------

def _on_banner(e: Event) -> None:
    C.banner(
        model=e.data["model"],
        base_url=e.data["base_url"],
        skill_count=e.data.get("skill_count", 0),
    )


def _on_status(e: Event) -> None:
    C.status_line(
        status.build_status_parts(
            model=e.data["model"],
            mode=e.data["mode"],
            tokens_in=e.data["tokens_in"],
            tokens_out=e.data["tokens_out"],
        )
    )


def _on_turn_end(e: Event) -> None:
    _activity.idle()
    C.turn_complete(
        elapsed=status.turn_elapsed(),
        tokens_in=e.data.get("tokens_in", 0),
        tokens_out=e.data.get("tokens_out", 0),
        tool_calls=_turn_tool_count,
    )
    status.turn_reset()


def _on_turn_interrupted(e: Event) -> None:
    _activity.idle()
    console.print()
    console.print(f"[log.warn]{ERROR} Interrupted[/log.warn] [muted]back to prompt[/muted]")
    status.turn_reset()


def _on_turn_max(e: Event) -> None:
    console.print()
    console.print(
        f"[log.error]{ERROR} Max tool turns reached[/log.error]  "
        "[muted]ending turn[/muted]"
    )
    status.turn_reset()


def _on_session_reset(e: Event) -> None:
    console.print(f"[kryth.core]{CORE}[/kryth.core] [muted]session cleared[/muted]")


def _on_plan(e: Event) -> None:
    C.section_header("planning", "section.plan")
    C.plan_panel(e.data["plan"])


def _on_plan_prose(e: Event) -> None:
    C.section_header("planning", "section.plan")
    C.plan_prose(e.data["text"])


def _on_plan_mode(e: Event) -> None:
    console.print()
    console.print(
        f"[kryth.core]{CORE}[/kryth.core] [log.warn]Planning[/log.warn]  "
        f"[muted]read-only tools only[/muted]"
    )


def _on_auto_skills(e: Event) -> None:
    names = e.data.get("skills") or []
    if not names:
        return
    console.print(f"[kryth.core]{CORE}[/kryth.core] [muted]skills[/muted] [title]{', '.join(names)}[/title]")


# -- LLM stream events ------------------------------------------------

_THINKING_MESSAGES = [
    "◈ Surveying repository...",
    "◈ Mapping project structure...",
    "◈ Analyzing dependencies...",
    "◈ Building execution strategy...",
    "◈ Simulating modifications...",
    "◈ Preparing patch...",
    "◈ Reviewing implementation...",
    "◈ Running verification...",
    "◈ Finalizing response...",
]
_thinking_idx = 0
_turn_tool_count = 0


def _on_turn_start(e: Event) -> None:
    global _turn_tool_count, _thinking_idx
    _turn_tool_count = 0
    _thinking_idx = 0
    status.mark_turn_start()


def _on_llm_waiting(e: Event) -> None:
    global _thinking_idx
    msg = e.data.get("message", "")
    if not msg or msg in ("Thinking", "waiting for model…", "waiting for model..."):
        msg = _THINKING_MESSAGES[_thinking_idx % len(_THINKING_MESSAGES)]
        _thinking_idx += 1
    _activity.waiting(msg)


def _on_llm_reasoning_start(e: Event) -> None:
    _activity.streaming()
    _stream.begin_reasoning()


def _on_llm_reasoning_chunk(e: Event) -> None:
    _activity.streaming()
    _stream.reasoning_chunk(e.data["piece"], elapsed=e.data.get("elapsed", 0.0))


def _on_llm_reasoning_end(e: Event) -> None:
    _stream.end_reasoning()
    _activity.idle()


def _on_llm_content_start(e: Event) -> None:
    _activity.streaming()
    _stream.begin_content()


def _on_llm_content_chunk(e: Event) -> None:
    _activity.streaming()
    _stream.content_chunk(e.data["piece"])


def _on_llm_content_end(e: Event) -> None:
    _stream.end_content(render_markdown=e.data.get("render_markdown", True))
    _activity.idle()


def _on_llm_usage(e: Event) -> None:
    # Token usage is tracked internally and shown in /status and Mission Complete.
    # Do not print a line on every turn — it floods the terminal.
    return


def _on_llm_error(e: Event) -> None:
    _activity.idle()
    _stream.force_newline()
    label = e.data.get("label", "llm")
    msg = e.data.get("message", "unknown error")
    console.print()
    console.print(f"[log.error]{ERROR} {label}[/log.error]  {msg}")
    if hint := e.data.get("hint"):
        console.print(f"[muted]{hint}[/muted]")


def _on_llm_retry(e: Event) -> None:
    delay = e.data.get("delay")
    line = (
        f"[log.warn]{CORE} retry {e.data['attempt']}/{e.data['total']}[/log.warn]  "
        f"[muted]{e.data['label']}: {e.data['reason']}"
    )
    if delay:
        line += f" · waiting {delay:.1f}s"
    line += "[/muted]"
    console.print(line)


def _on_llm_hermes_recovery(e: Event) -> None:
    # Silently recover tool calls without showing debug messages
    pass


def _on_llm_degenerate(e: Event) -> None:
    console.print()
    console.print(
        f"[log.warn]{ERROR} Stream stopped[/log.warn]  "
        "[muted]model was looping; salvaging partial response[/muted]"
    )


# -- tool dispatch ----------------------------------------------------

def _on_tool_start(e: Event) -> None:
    global _turn_tool_count
    _turn_tool_count += 1
    _stream.force_newline()
    summary = _summarize_args(e.data["name"], e.data.get("args") or {})
    C.tool_header(e.data["name"], summary)


def _on_tool_result(e: Event) -> None:
    first, extra = _result_first_line(e.data["result"])
    C.tool_result(first, extra, error=e.data.get("error", False))


def _on_tool_error(e: Event) -> None:
    C.tool_error_line(e.data["message"])


def _on_tool_cancelled(e: Event) -> None:
    C.tool_status_line("cancelled by user", style="log.warn")


def _on_tool_coerced(e: Event) -> None:
    # Silently coerce arguments without showing debug messages
    pass


def _on_tool_denied(e: Event) -> None:
    C.tool_error_line(f"permission denied · {e.data['tool']}")


def _on_tool_hook_blocked(e: Event) -> None:
    C.tool_error_line(f"hook blocked · {e.data['message']}")


# -- file / shell -----------------------------------------------------

def _on_write_preview(e: Event) -> None:
    file_updates.on_write_preview(e)


def _on_diff(e: Event) -> None:
    file_updates.on_diff(e)


def _on_shell_run(e: Event) -> None:
    # The header is now part of the unified command panel that lands on
    # SHELL_END. We leave SHELL_RUN as a quiet event so subscribers
    # (telemetry, transcripts) can still observe the lifecycle.
    return


def _on_shell_end(e: Event) -> None:
    summary = summarize_output(e.data["output"])
    command_panel.render_command_panel(
        command=e.data["command"],
        summary=summary,
        exit_code=e.data["exit_code"],
        timeout=e.data["timeout"],
        note=e.data.get("note"),
    )


def _on_run_summary(e: Event) -> None:
    C.run_summary_panel(e.data["summary"])


def _on_todos(e: Event) -> None:
    C.todos_panel(e.data["items"])


# -- subagent --------------------------------------------------------

def _on_subagent_start(e: Event) -> None:
    C.subagent_open(depth=e.data["depth"], description=e.data["description"])


def _on_subagent_end(e: Event) -> None:
    C.subagent_close(depth=e.data["depth"])


# -- compaction ------------------------------------------------------

def _on_compact_start(e: Event) -> None:
    console.print()
    console.print(
        f"[muted]{CORE} compacting {e.data['count']} older messages (~{e.data['tokens']:,} tokens)[/muted]"
    )
    _activity.compacting(
        f"◈ Synchronizing memory... ({e.data['count']} messages)"
    )


def _on_compact_fallback(e: Event) -> None:
    dropped_msgs = e.data.get("dropped_messages", 0)
    dropped_chars = e.data.get("dropped_chars", 0)
    if dropped_chars:
        console.print(
            f"[log.warn]{ERROR} summarizer unavailable[/log.warn]  "
            f"[muted]fallback compactor elided ~{dropped_chars:,} chars "
            f"across {dropped_msgs} message{'s' if dropped_msgs != 1 else ''}. "
            f"Earlier context is now lossy; re-ask for it if needed.[/muted]"
        )
    else:
        console.print(
            f"[log.warn]{ERROR} summarizer unavailable[/log.warn]  "
            "[muted]fallback compactor ran with no detail loss.[/muted]"
        )


# -- generic log -----------------------------------------------------

def _on_log(e: Event) -> None:
    level = e.data.get("level", "info")
    msg = e.data.get("message", "")
    console.print(f"[log.{level}]{msg}[/log.{level}]")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_HANDLERS: dict[EventKind, Callable[[Event], None]] = {
    EventKind.BANNER: _on_banner,
    EventKind.STATUS: _on_status,
    EventKind.TURN_START: _on_turn_start,
    EventKind.TURN_END: _on_turn_end,
    EventKind.TURN_INTERRUPTED: _on_turn_interrupted,
    EventKind.TURN_MAX_TURNS: _on_turn_max,
    EventKind.SESSION_RESET: _on_session_reset,

    EventKind.PLAN: _on_plan,
    EventKind.PLAN_PROSE: _on_plan_prose,
    EventKind.PLAN_MODE: _on_plan_mode,
    EventKind.AUTO_SKILLS: _on_auto_skills,

    EventKind.LLM_WAITING: _on_llm_waiting,
    EventKind.LLM_REASONING_START: _on_llm_reasoning_start,
    EventKind.LLM_REASONING_CHUNK: _on_llm_reasoning_chunk,
    EventKind.LLM_REASONING_END: _on_llm_reasoning_end,
    EventKind.LLM_CONTENT_START: _on_llm_content_start,
    EventKind.LLM_CONTENT_CHUNK: _on_llm_content_chunk,
    EventKind.LLM_CONTENT_END: _on_llm_content_end,
    EventKind.LLM_USAGE: _on_llm_usage,
    EventKind.LLM_ERROR: _on_llm_error,
    EventKind.LLM_RETRY: _on_llm_retry,
    EventKind.LLM_HERMES_RECOVERY: _on_llm_hermes_recovery,
    EventKind.LLM_DEGENERATE: _on_llm_degenerate,

    EventKind.TOOL_START: _on_tool_start,
    EventKind.TOOL_RESULT: _on_tool_result,
    EventKind.TOOL_ERROR: _on_tool_error,
    EventKind.TOOL_CANCELLED: _on_tool_cancelled,
    EventKind.TOOL_COERCED: _on_tool_coerced,
    EventKind.TOOL_DENIED: _on_tool_denied,
    EventKind.TOOL_HOOK_BLOCKED: _on_tool_hook_blocked,

    EventKind.WRITE_PREVIEW: _on_write_preview,
    EventKind.DIFF: _on_diff,
    EventKind.SHELL_RUN: _on_shell_run,
    EventKind.SHELL_END: _on_shell_end,
    EventKind.TODOS: _on_todos,
    EventKind.RUN_SUMMARY: _on_run_summary,

    EventKind.SUBAGENT_START: _on_subagent_start,
    EventKind.SUBAGENT_END: _on_subagent_end,

    EventKind.COMPACT_START: _on_compact_start,
    EventKind.COMPACT_FALLBACK: _on_compact_fallback,

    EventKind.LOG: _on_log,
}


def _dispatch(event: Event) -> None:
    handler = _HANDLERS.get(event.kind)
    if handler is None:
        # Unknown event: log it (only at DEBUG) but never crash.
        return
    handler(event)


_unsubscribe: Callable[[], None] | None = None


def install() -> None:
    """Wire the renderer to the bus. Idempotent."""
    global _unsubscribe
    if _unsubscribe is not None:
        return
    _unsubscribe = BUS.subscribe(_dispatch)


def uninstall() -> None:
    global _unsubscribe
    if _unsubscribe is None:
        return
    _unsubscribe()
    _unsubscribe = None
