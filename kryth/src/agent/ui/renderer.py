"""KRYTH terminal renderer — clean autonomous engineering console.

Rules:
  1. Never show tool names, JSON, XML, raw args, retry loops.
  2. Silent tools (reads, searches) produce zero output.
  3. Action tools produce one clean timeline line.
  4. Commands show a structured metric panel, never raw logs.
  5. Debug mode (KRYTH_DEBUG_UI=1) shows everything raw.
  6. The model's assistant reply text is shown as-is (already filtered
     for <think>/<tool_call> tags by StreamPrinter.content_chunk).
"""

from __future__ import annotations

import os
import re
import sys
import time as _time
from typing import Callable

from rich.text import Text

from agent.ui import components as C
from agent.ui import command_panel
from agent.ui import status_manager as status
from agent.ui import updates as file_updates
from agent.ui.activity import ActivityIndicator
from agent.ui.console import console
from agent.ui.events import BUS, Event, EventKind
from agent.ui.mission_console import mission_console, emit_timeline, is_silent, label
from agent.ui.panels import _print_panel
from agent.ui.streaming import StreamPrinter
from agent.ui.summarizer import summarize_output
from agent.ui.theme import CORE, DOT, ERROR, WAITING

_FORCE_DEBUG = os.environ.get("KRYTH_DEBUG_UI", "").lower() in {"1", "true", "yes"}

_activity = ActivityIndicator()
_stream = StreamPrinter()
_turn_tool_count = 0
_thinking_idx = 0
_shell_start: dict[str, float] = {}   # command → monotonic start time
_last_todos_sig: str = ""              # dedup key for todo panel
_turn_stats_cache: dict = {}           # elapsed, tokens_in, tokens_out from turn end

_ACTIVITY_MSGS = [
    "Analyzing project",
    "Mapping architecture",
    "Validating dependencies",
    "Generating execution plan",
    "Reviewing code",
    "Preparing changes",
    "Verifying build",
    "Running checks",
    "Synthesizing results",
    "Finalizing implementation",
]
_activity_idx = 0


def _is_debug() -> bool:
    try:
        from agent.ui.ui_state import ui_state, UILayer
        return _FORCE_DEBUG or ui_state.get_layer() == UILayer.DEBUG
    except Exception:
        return _FORCE_DEBUG


# ── Lifecycle ────────────────────────────────────────────────────────────────

_session_model: str = ""
_session_base_url: str = ""
_session_tools: int = 0


def _on_banner(e: Event) -> None:
    global _session_model, _session_base_url, _session_tools
    _session_model = e.data.get("model", "") or ""
    _session_base_url = e.data.get("base_url", "") or ""
    _session_tools = e.data.get("skill_count", 0) or 0
    C.banner(
        model=_session_model,
        base_url=_session_base_url,
        skill_count=_session_tools,
    )


def _on_turn_start(e: Event) -> None:
    global _turn_tool_count, _thinking_idx, _activity_idx, _last_todos_sig
    _turn_tool_count = 0
    _thinking_idx = 0
    _activity_idx = 0
    _last_todos_sig = ""
    status.mark_turn_start()
    mission_console.reset_turn()
    mission_console.configure(debug=_is_debug())


def _on_turn_end(e: Event) -> None:
    _activity.idle()
    elapsed = status.turn_elapsed()
    C.turn_complete(
        elapsed=elapsed,
        tokens_in=e.data.get("tokens_in", 0),
        tokens_out=e.data.get("tokens_out", 0),
        tool_calls=_turn_tool_count,
    )
    # Stash stats so _on_run_summary can include them
    _turn_stats_cache.clear()
    _turn_stats_cache.update({
        "elapsed": elapsed,
        "tokens_in": e.data.get("tokens_in", 0),
        "tokens_out": e.data.get("tokens_out", 0),
    })
    # UI v3 — premium status bar + debug panel.
    try:
        _tokens = e.data.get("tokens_in", 0) + e.data.get("tokens_out", 0)
        _tel_on = bool(os.environ.get("KRYTH_RUNTIME_TELEMETRY"))
        from agent.ui.components import _provider_label, _adapter_label
        C.status_bar(
            provider=_provider_label(_session_model, _session_base_url),
            tools=_turn_tool_count,
            tokens=_tokens,
            adapter=_adapter_label(),
            telemetry_on=_tel_on,
        )
        C.debug_panel(model=_session_model, base_url=_session_base_url, tools=_session_tools)
    except Exception:
        pass

    status.turn_reset()
    try:
        from agent.ui.ui_state import ui_state
        ui_state.turn_reset()
    except Exception:
        pass


def _on_turn_interrupted(e: Event) -> None:
    _activity.idle()
    console.print()
    console.print(Text.assemble(
        (ERROR, "log.warn"), ("  Interrupted", "log.warn"),
        ("  —  back to prompt", "muted"),
    ))
    status.turn_reset()


def _on_turn_max(e: Event) -> None:
    console.print()
    console.print(Text.assemble(
        (ERROR, "log.warn"), ("  Turn limit reached", "log.warn"),
        ("  —  reply to continue", "muted"),
    ))
    status.turn_reset()


def _on_session_reset(e: Event) -> None:
    console.print(Text.assemble(
        (CORE, "kryth.core"), ("  Session cleared", "muted"),
    ))


def _on_status(e: Event) -> None:
    C.status_line(
        status.build_status_parts(
            model=e.data["model"],
            mode=e.data["mode"],
            tokens_in=e.data["tokens_in"],
            tokens_out=e.data["tokens_out"],
        )
    )


# ── Planning ─────────────────────────────────────────────────────────────────

def _on_plan(e: Event) -> None:
    if _is_debug():
        C.plan_panel(e.data["plan"])


def _on_plan_prose(e: Event) -> None:
    if _is_debug():
        C.plan_prose(e.data["text"])


def _on_plan_mode(e: Event) -> None:
    console.print()
    console.print(Text.assemble(
        (CORE, "kryth.core"), ("  Plan mode", "log.warn"),
        ("  —  read-only until finalized", "muted"),
    ))


def _on_auto_skills(e: Event) -> None:
    names = e.data.get("skills") or []
    if names and _is_debug():
        console.print(Text.assemble(
            (CORE, "kryth.core"), ("  skills  ", "muted"),
            (", ".join(names), "title"),
        ))


# ── LLM stream ───────────────────────────────────────────────────────────────

def _on_llm_waiting(e: Event) -> None:
    global _activity_idx
    try:
        mc_mod = sys.modules.get("agent.ui.mission_control")
        if mc_mod is not None and mc_mod.get_active_mc() is not None:
            return
    except Exception:
        pass
    msg = e.data.get("message") or _ACTIVITY_MSGS[_activity_idx % len(_ACTIVITY_MSGS)]
    _activity_idx += 1
    _activity.waiting(f"◈ {msg}…")


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
    # Record for budget tracking
    try:
        from agent.supervisor.budget import budget_controller
        budget_controller.record_tokens(
            e.data.get("turn_in", 0),
            e.data.get("turn_out", 0),
        )
    except Exception:
        pass
    # Live token display after every LLM turn
    try:
        turn_in  = e.data.get("turn_in", 0)
        turn_out = e.data.get("turn_out", 0)
        sess_in  = e.data.get("session_in", 0)
        sess_out = e.data.get("session_out", 0)
        total    = sess_in + sess_out
        console.print(
            f"  [muted]tokens this turn:[/muted] "
            f"[dim]{turn_in:,}[/dim][muted] in[/muted]  "
            f"[dim]{turn_out:,}[/dim][muted] out[/muted]"
            f"   [muted]session total:[/muted] [kryth.core]{total:,}[/kryth.core]"
        )
    except Exception:
        pass


def _on_token_budget(e: Event) -> None:
    """Show pre-call token breakdown when KRYTH_TOKEN_TELEMETRY=1."""
    import os
    if os.environ.get("KRYTH_TOKEN_TELEMETRY", "0") not in ("1", "true", "yes"):
        return
    est    = e.data.get("est_before", 0)
    tools  = e.data.get("tools_tok", 0)
    hist   = e.data.get("history_tok", 0)
    count  = e.data.get("tools_count", 0)
    sys_tok = max(0, est - tools - hist)
    console.print(
        f"  [muted]ctx budget:[/muted]"
        f" [dim]sys≈{sys_tok:,}[/dim]"
        f" [dim]tools≈{tools:,}({count})[/dim]"
        f" [dim]hist≈{hist:,}[/dim]"
        f" [kryth.core]total≈{est:,}[/kryth.core]"
    )


def _on_llm_error(e: Event) -> None:
    _activity.idle()
    _stream.force_newline()
    msg = e.data.get("message", "unknown error")
    console.print()
    console.print(Text.assemble(
        (ERROR, "log.error"), ("  Error  —  ", "log.error"), (msg[:120], "muted"),
    ))
    if hint := e.data.get("hint"):
        console.print(Text.assemble(("  ", ""), (hint[:120], "muted")))


def _on_llm_retry(e: Event) -> None:
    if _is_debug():
        console.print(
            f"[log.warn]{CORE} retry {e.data['attempt']}/{e.data['total']}[/log.warn]  "
            f"[muted]{e.data['label']}: {e.data['reason']}[/muted]"
        )


def _on_llm_hermes_recovery(e: Event) -> None:
    pass


def _on_llm_degenerate(e: Event) -> None:
    if _is_debug():
        console.print(Text.assemble(
            (ERROR, "log.warn"), ("  stream degraded", "muted"),
        ))


# ── Tool dispatch ────────────────────────────────────────────────────────────

def _on_tool_start(e: Event) -> None:
    global _turn_tool_count
    _turn_tool_count += 1
    _stream.force_newline()

    name = e.data["name"]
    args = e.data.get("args") or {}

    # In DAG/SWARM parallel mode: update spinner only — no raw log spam.
    # The dashboard cards show per-agent progress; terminal sees agent cards only.
    try:
        from agent.ui.streaming import _parallel_mode as _pm
    except Exception:
        _pm = False

    lbl = label(name, args)
    _activity.waiting(f"◈ {lbl}…")

    if not _pm:
        # Route through mission_console — it decides what's visible
        mission_console.on_tool_start(name, args)

    # Update engineering state tracker
    try:
        from agent.ui.ui_state import ui_state
        ui_state.add_eng_action(lbl, "running")
        ui_state.inc_tool()
    except Exception:
        pass


def _on_tool_result(e: Event) -> None:
    name = getattr(mission_console, "_current_tool", "") or ""
    error = e.data.get("error", False)
    result_str = str(e.data.get("result", ""))

    try:
        from agent.ui.streaming import _parallel_mode as _pm
    except Exception:
        _pm = False

    if not _pm:
        mission_console.on_tool_result(name, result_str, error)

    # In debug only: show first result line (never in parallel mode)
    if _is_debug() and not _pm:
        lines = result_str.strip().splitlines()
        first = lines[0][:120] if lines else "(empty)"
        extra = max(0, len(lines) - 1)
        C.tool_result(first, extra, error=error)


def _on_tool_error(e: Event) -> None:
    # In DAG/SWARM parallel mode suppress tool errors from the main timeline —
    # per-agent card panels show agent status; raw error lines are noise.
    try:
        from agent.ui.streaming import _parallel_mode as _pm
    except Exception:
        _pm = False
    if _pm:
        return  # DAG mode: tool errors hidden from main terminal

    msg = e.data.get("message", "")
    if _is_debug():
        C.tool_error_line(msg)
    else:
        short = msg[:80]
        emit_timeline(f"Issue  —  {short}", "warn")


def _on_tool_cancelled(e: Event) -> None:
    emit_timeline("Action cancelled", "warn")


def _on_tool_coerced(e: Event) -> None:
    pass


def _on_tool_denied(e: Event) -> None:
    tool = e.data.get("tool", "")
    lbl = label(tool)
    emit_timeline(f"{lbl}  —  access denied", "warn")


def _on_tool_hook_blocked(e: Event) -> None:
    if _is_debug():
        C.tool_error_line(f"hook blocked  ·  {e.data.get('message', '')}")


# ── File / shell ─────────────────────────────────────────────────────────────

def _on_write_preview(e: Event) -> None:
    path = e.data["path"]
    emit_timeline(f"File created  —  {os.path.basename(path)}", "success")
    file_updates.on_write_preview(e)


def _on_diff(e: Event) -> None:
    path = e.data.get("path") or ""
    emit_timeline(f"Changes applied  —  {os.path.basename(path) or 'file'}", "success")
    file_updates.on_diff(e)


def _on_shell_run(e: Event) -> None:
    _shell_start[e.data.get("command", "")] = _time.monotonic()


def _on_shell_end(e: Event) -> None:
    command = e.data["command"]
    exit_code = e.data["exit_code"]
    output = e.data["output"]
    timeout = e.data["timeout"]
    note = e.data.get("note")

    start = _shell_start.pop(command, None)
    duration = _time.monotonic() - start if start is not None else None

    if _is_debug():
        summary = summarize_output(output)
        command_panel.render_command_panel(
            command=command, summary=summary,
            exit_code=exit_code, timeout=timeout, note=note,
        )
        return

    # Clean structured summary panel
    _render_command_summary(command, output, exit_code, duration=duration)


def _render_command_summary(command: str, output: str, exit_code: int, *, duration: float | None = None) -> None:
    import rich.box
    from rich.panel import Panel
    from rich.table import Table

    summary = summarize_output(output)
    success = exit_code == 0

    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=12)
    body.add_column(no_wrap=True)

    status_text = "Success" if success else f"Failed  (exit {exit_code})"
    status_style = "term.success" if success else "term.failed"
    body.add_row(Text("Status", style="muted"), Text(status_text, style=status_style))

    if duration is not None:
        body.add_row(Text("Duration", style="muted"), Text(f"{duration:.2f}s", style="title"))

    if summary.headline:
        body.add_row(Text("Result", style="muted"), Text(summary.headline, style="title"))
    if summary.errors:
        body.add_row(Text("Errors", style="muted"), Text(str(summary.errors), style="term.failed"))
    if summary.warnings:
        body.add_row(Text("Warnings", style="muted"), Text(str(summary.warnings), style="log.warn"))
    body.add_row(Text("Output", style="muted"), Text(f"{summary.total_lines} lines", style="muted"))

    short_cmd = command if len(command) <= 70 else command[:67] + "…"
    glyph = CORE if success else ERROR
    glyph_style = "log.success" if success else "log.error"
    border = "term.success" if success else "term.failed"

    _print_panel(Panel(
        body,
        title=Text.assemble((glyph, glyph_style), ("  $ ", "muted"), (short_cmd, "title")),
        title_align="left",
        border_style=border,
        padding=(0, 2),
        expand=False,
        box=rich.box.ROUNDED,
    ))

    kind = "success" if success else "error"
    # Show the first word of the command as the label (e.g. "npm", "pytest")
    cmd_word = command.strip().split()[0] if command.strip() else "command"
    emit_timeline(f"✓ {cmd_word}" if success else f"✗ {cmd_word} failed", kind)


def _render_turn_summary(s: dict) -> None:
    """Grouped section summary — shown at turn end when side-effects occurred."""
    import rich.box
    from rich.console import Group as RGroup
    from rich.padding import Padding
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text as RText
    from agent.ui.mission_console import _SECTION_GROUPS
    from agent.ui.theme import LEFT_MARGIN, PANEL_MARGIN

    has_files = s.get("files_written") or s.get("files_edited") or s.get("files_deleted")
    has_cmds  = s.get("shell_ok", 0) or s.get("shell_fail", 0)
    if not has_files and not has_cmds:
        return

    tool_counts: dict = s.get("tool_counts") or {}
    elapsed    = s.get("elapsed")
    total_tools = s.get("tools_called", 0)

    # Group tool counts by section
    section_tools: dict[str, dict[str, int]] = {}
    for tool, count in tool_counts.items():
        section = _SECTION_GROUPS.get(tool, "")
        if section:
            section_tools.setdefault(section, {})[tool] = count

    def _names(paths: list, cap: int = 5) -> str:
        names = [os.path.basename(p) for p in paths[:cap]]
        tail  = f"  +{len(paths) - cap} more" if len(paths) > cap else ""
        return "  ·  ".join(names) + tail

    rows: list[tuple] = []  # (label_text, value_text)

    # Section-based tool rows
    _SECTION_ORDER = [
        "Repository Analysis", "Code Search",
        "File Generation", "Code Changes", "File Changes",
        "Dependencies", "Build & Test",
        "Browser Verification", "Version Control", "Agent Deployment",
    ]
    for sec in _SECTION_ORDER:
        tools = section_tools.get(sec)
        if not tools:
            continue
        parts = [f"{t.replace('_', ' ')}  ×{c}" for t, c in sorted(tools.items(), key=lambda x: -x[1])[:6]]
        rows.append((
            RText(sec, style="muted"),
            RText("  ·  ".join(parts), style="kryth.core"),
        ))

    if s.get("files_written"):
        rows.append((RText("Files Created", style="muted"), RText(_names(s["files_written"]), style="log.success")))
    if s.get("files_edited"):
        rows.append((RText("Files Edited",  style="muted"), RText(_names(s["files_edited"]),  style="accent")))
    if s.get("files_deleted"):
        rows.append((RText("Files Deleted", style="muted"), RText(_names(s["files_deleted"]), style="log.error")))

    cmds = s.get("last_commands") or []
    if cmds:
        ok   = s.get("shell_ok", 0)
        fail = s.get("shell_fail", 0)
        sfx  = f"  ({ok} ok, {fail} failed)" if fail else f"  ({ok} passed)"
        rows.append((RText("Commands", style="muted"), RText(_names(cmds) + sfx, style="log.success" if not fail else "log.warn")))

    # Compact metrics footer
    metrics: list[str] = []
    if elapsed:
        metrics.append(f"Duration  {elapsed:.1f}s")
    if total_tools:
        metrics.append(f"Tools  {total_tools}")
    if s.get("retries"):
        metrics.append(f"Retries  {s['retries']}")
    if metrics:
        rows.append((RText("", style=""), RText("  ·  ".join(metrics), style="muted")))

    tokens_in  = s.get("tokens_in", 0)
    tokens_out = s.get("tokens_out", 0)
    if tokens_in or tokens_out:
        rows.append((
            RText("Tokens", style="muted"),
            RText(f"{tokens_in + tokens_out:,}  ({tokens_in:,} in · {tokens_out:,} out)", style="muted"),
        ))

    body = Table.grid(padding=(0, 3), expand=False)
    body.add_column(no_wrap=True, style="muted", min_width=16)
    body.add_column(overflow="fold")
    for lbl, val in rows:
        body.add_row(lbl, val)

    is_err    = s.get("status") in ("api_error", "interrupted", "max_turns")
    glyph     = ERROR if is_err else CORE
    glyph_sty = "log.error" if is_err else "log.success"

    panel = Panel(
        body,
        title=RText.assemble((glyph, glyph_sty), ("  Session Summary", glyph_sty)),
        title_align="left",
        border_style=glyph_sty,
        padding=(0, PANEL_MARGIN),
        expand=False,
        box=rich.box.ROUNDED,
    )
    console.print()
    console.print(Padding(panel, (0, 0, 0, LEFT_MARGIN)))


def _on_run_summary(e: Event) -> None:
    data = dict(e.data["summary"])
    # Merge cached turn stats (elapsed, tool counts) but let explicit token
    # values in the event win — they come directly from session.cumulative_*
    # and are always accurate. Stale cache values would show wrong numbers.
    for k, v in _turn_stats_cache.items():
        if k not in ("tokens_in", "tokens_out") or k not in data:
            data.setdefault(k, v)
    _render_turn_summary(data)


def _on_todos(e: Event) -> None:
    global _last_todos_sig
    items = e.data["items"]
    # Only render when the list composition changes meaningfully.
    # Signature: total count + completed count + first 3 texts.
    texts = "".join(i.get("text", "")[:20] for i in items[:3])
    done = sum(1 for i in items if i.get("status") == "completed")
    sig = f"{len(items)}:{done}:{texts}"
    if sig == _last_todos_sig:
        return
    _last_todos_sig = sig
    C.todos_panel(items)


# ── Subagents ────────────────────────────────────────────────────────────────

def _on_subagent_start(e: Event) -> None:
    desc = e.data.get("description", "")
    clean = re.sub(r'^\[\d+\]\s*', '', desc)
    emit_timeline(f"Team member deployed  —  {clean[:60]}")
    try:
        from agent import ui
        ui.agent_update(clean[:30], "running", clean[:50], 0)
    except Exception:
        pass


def _on_subagent_end(e: Event) -> None:
    emit_timeline("Team member complete", "success")


# ── Compaction ───────────────────────────────────────────────────────────────

def _on_compact_start(e: Event) -> None:
    _activity.compacting("◈ Synchronizing memory…")


def _on_compact_fallback(e: Event) -> None:
    if _is_debug():
        dropped = e.data.get("dropped_chars", 0)
        if dropped:
            console.print(f"[muted]context compacted  ·  ~{dropped:,} chars elided[/muted]")


# ── Generic log ──────────────────────────────────────────────────────────────

def _on_log(e: Event) -> None:
    level = e.data.get("level", "info")
    msg = e.data.get("message", "")
    if level in ("info", "debug") and not _is_debug():
        return
    console.print(f"[log.{level}]{msg}[/log.{level}]")


# ── Next-Gen events (optional, used by supervisor/mission tools) ──────────────

def _on_mission_start(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.mission_start(e.data.get("goal", ""), e.data.get("estimated_duration", ""))
    except Exception:
        pass
    emit_timeline(f"Mission started  —  {e.data.get('goal', '')[:60]}")


def _on_mission_progress(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.mission_progress(e.data.get("percent", 0), e.data.get("stage", ""))
    except Exception:
        pass
    stage = e.data.get("stage", "")
    if stage:
        _activity.waiting(f"◈ {stage}…")


def _on_mission_complete(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.mission_complete(e.data.get("summary", {}))
    except Exception:
        pass
    _activity.idle()
    emit_timeline("Mission complete", "success")


def _on_mission_failed(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.mission_failed(e.data.get("reason", ""))
    except Exception:
        pass
    _activity.idle()
    emit_timeline(f"Mission failed  —  {e.data.get('reason', '')[:60]}", "error")


def _on_agent_update(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.update_agent(
            e.data.get("name", ""), e.data.get("status", "idle"),
            e.data.get("task", ""), e.data.get("progress", 0),
        )
    except Exception:
        pass


def _on_timeline_event(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.add_timeline_event(e.data.get("message", ""), e.data.get("kind", "info"))
    except Exception:
        pass
    try:
        from agent.ui.timeline import append_timeline_line
        append_timeline_line(e.data.get("message", ""), e.data.get("kind", "info"))
    except Exception:
        pass


def _on_engineering_action(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.add_eng_action(e.data.get("label", ""), e.data.get("status", "running"), e.data.get("detail", ""))
    except Exception:
        pass


def _on_engineering_section(e: Event) -> None:
    try:
        from agent.ui.ui_state import ui_state
        ui_state.set_eng_section(e.data.get("title", ""))
    except Exception:
        pass


def _on_layer_change(e: Event) -> None:
    layer = e.data.get("layer", "executive")
    try:
        from agent.ui.ui_state import ui_state
        ui_state.set_layer(layer)
    except Exception:
        pass
    mission_console.configure(debug=_is_debug())
    console.print(Text.assemble((CORE, "kryth.core"), (f"  Layer: {layer}", "muted")))


def _on_approval_batch(e: Event) -> None:
    try:
        from agent.ui.approval_ui import render_approval_panel
        render_approval_panel(e.data.get("items", []), e.data.get("risk", "low"), e.data.get("estimated_time", ""))
    except Exception:
        pass


def _on_reflection(e: Event) -> None:
    try:
        from agent.ui.reflection_ui import render_reflection
        render_reflection(e.data.get("worked", ""), e.data.get("failed", ""), e.data.get("learned", ""))
    except Exception:
        pass


def _on_memory_display(e: Event) -> None:
    try:
        from agent.ui.memory_ui import render_memory
        render_memory(e.data.get("similar_tasks", 0), e.data.get("best_workflow", ""), e.data.get("success_rate", 0))
    except Exception:
        pass


def _on_terminal_summary(e: Event) -> None:
    try:
        from agent.ui.terminal_dash import render_terminal_summary
        render_terminal_summary(e.data.get("command", ""), e.data.get("status", "success"), e.data.get("metrics", {}), e.data.get("expandable_log", ""))
    except Exception:
        pass


def _on_dag_update(e: Event) -> None:
    pass  # DAG visualization only in engineering/debug layers


# ── Runtime v2 spec-named events ─────────────────────────────────────────────
# These provide the canonical event vocabulary. Normal-mode visuals are already
# produced by the granular events (content chunks, tool start/result, turn end),
# so these consume cleanly without double-rendering. Debug mode surfaces them.

def _on_assistant_message(e: Event) -> None:
    if _is_debug():
        txt = str(e.data.get("text", ""))[:200]
        console.print(Text.assemble((CORE, "kryth.core"), ("  assistant_message  ", "muted"), (txt, "title")))


def _on_tool_arguments(e: Event) -> None:
    if _is_debug():
        console.print(Text.assemble(
            (CORE, "kryth.core"), ("  tool_arguments  ", "muted"),
            (f"{e.data.get('tool', '')}  {e.data.get('arguments', {})}"[:160], "muted"),
        ))


def _on_tool_finish(e: Event) -> None:
    if _is_debug():
        ok = e.data.get("ok", True)
        console.print(Text.assemble(
            (CORE, "kryth.core"), ("  tool_finish  ", "muted"),
            (f"{e.data.get('tool', '')}  {'ok' if ok else 'failed'}", "muted"),
        ))


def _on_complete(e: Event) -> None:
    # Turn-end visuals are already emitted via TURN_END / RUN_SUMMARY; this is
    # the canonical completion signal and stays silent in normal mode.
    if _is_debug():
        console.print(Text.assemble(
            (CORE, "kryth.core"), ("  complete  ", "muted"),
            (f"{e.data.get('status', 'done')}  turns={e.data.get('turns_used', 0)}", "muted"),
        ))


# ── Handler registry ─────────────────────────────────────────────────────────

_HANDLERS: dict[EventKind, Callable[[Event], None]] = {
    EventKind.BANNER:           _on_banner,
    EventKind.STATUS:           _on_status,
    EventKind.TURN_START:       _on_turn_start,
    EventKind.TURN_END:         _on_turn_end,
    EventKind.TURN_INTERRUPTED: _on_turn_interrupted,
    EventKind.TURN_MAX_TURNS:   _on_turn_max,
    EventKind.SESSION_RESET:    _on_session_reset,
    EventKind.PLAN:             _on_plan,
    EventKind.PLAN_PROSE:       _on_plan_prose,
    EventKind.PLAN_MODE:        _on_plan_mode,
    EventKind.AUTO_SKILLS:      _on_auto_skills,
    EventKind.LLM_WAITING:           _on_llm_waiting,
    EventKind.LLM_REASONING_START:   _on_llm_reasoning_start,
    EventKind.LLM_REASONING_CHUNK:   _on_llm_reasoning_chunk,
    EventKind.LLM_REASONING_END:     _on_llm_reasoning_end,
    EventKind.LLM_CONTENT_START:     _on_llm_content_start,
    EventKind.LLM_CONTENT_CHUNK:     _on_llm_content_chunk,
    EventKind.LLM_CONTENT_END:       _on_llm_content_end,
    EventKind.LLM_USAGE:             _on_llm_usage,
    EventKind.TOKEN_BUDGET:          _on_token_budget,
    EventKind.LLM_ERROR:             _on_llm_error,
    EventKind.LLM_RETRY:             _on_llm_retry,
    EventKind.LLM_HERMES_RECOVERY:   _on_llm_hermes_recovery,
    EventKind.LLM_DEGENERATE:        _on_llm_degenerate,
    EventKind.TOOL_START:       _on_tool_start,
    EventKind.TOOL_RESULT:      _on_tool_result,
    EventKind.TOOL_ERROR:       _on_tool_error,
    EventKind.TOOL_CANCELLED:   _on_tool_cancelled,
    EventKind.TOOL_COERCED:     _on_tool_coerced,
    EventKind.TOOL_DENIED:      _on_tool_denied,
    EventKind.TOOL_HOOK_BLOCKED: _on_tool_hook_blocked,
    EventKind.WRITE_PREVIEW:    _on_write_preview,
    EventKind.DIFF:             _on_diff,
    EventKind.SHELL_RUN:        _on_shell_run,
    EventKind.SHELL_END:        _on_shell_end,
    EventKind.TODOS:            _on_todos,
    EventKind.RUN_SUMMARY:      _on_run_summary,
    EventKind.SUBAGENT_START:   _on_subagent_start,
    EventKind.SUBAGENT_END:     _on_subagent_end,
    EventKind.COMPACT_START:    _on_compact_start,
    EventKind.COMPACT_FALLBACK: _on_compact_fallback,
    EventKind.LOG:              _on_log,
    # Next-gen supervisor/mission events
    EventKind.MISSION_START:         _on_mission_start,
    EventKind.MISSION_PROGRESS:      _on_mission_progress,
    EventKind.MISSION_COMPLETE:      _on_mission_complete,
    EventKind.MISSION_FAILED:        _on_mission_failed,
    EventKind.AGENT_UPDATE:          _on_agent_update,
    # Agent lifecycle events — route through existing agent_update handler
    EventKind.AGENT_CREATED:         _on_agent_update,
    EventKind.AGENT_TASK_START:      _on_agent_update,
    EventKind.AGENT_TASK_DONE:       _on_agent_update,
    EventKind.AGENT_FAILED:          _on_agent_update,
    EventKind.WORK_STOLEN:           _on_agent_update,
    EventKind.QUEUE_STATUS:          _on_agent_update,
    EventKind.TIMELINE_EVENT:        _on_timeline_event,
    EventKind.ENGINEERING_ACTION:    _on_engineering_action,
    EventKind.ENGINEERING_SECTION:   _on_engineering_section,
    EventKind.LAYER_CHANGE:          _on_layer_change,
    EventKind.APPROVAL_BATCH:        _on_approval_batch,
    EventKind.REFLECTION:            _on_reflection,
    EventKind.MEMORY_DISPLAY:        _on_memory_display,
    EventKind.TERMINAL_SUMMARY:      _on_terminal_summary,
    EventKind.DAG_UPDATE:            _on_dag_update,
    # Runtime v2 spec-named events
    EventKind.ASSISTANT_MESSAGE:     _on_assistant_message,
    EventKind.TOOL_ARGUMENTS:        _on_tool_arguments,
    EventKind.TOOL_FINISH:           _on_tool_finish,
    EventKind.COMPLETE:              _on_complete,
}


def _dispatch(event: Event) -> None:
    # Clean execution view: during orchestrated (DAG/SWARM) missions, suppress
    # internal events (tool calls, reasoning, planning, logs) from the main view
    # unless the user opened /agents, /logs, or /debug. Presentation-only.
    try:
        from agent.ui.clean_view import should_render
        if not should_render(event.kind):
            return
    except Exception:
        pass
    handler = _HANDLERS.get(event.kind)
    if handler:
        handler(event)


_unsubscribe: Callable[[], None] | None = None


def install() -> None:
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
