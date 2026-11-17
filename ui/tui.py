from __future__ import annotations

import asyncio
import re
import time

from io import StringIO
from typing import Any, Callable

from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text
from prompt_toolkit.keys import Keys
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from config.config import ApprovalPolicy, Config, ExecutionMode
from tools.base import ToolConfirmation
from ui.components import (
    APPROVE_KEYS,
    REASONING_LABEL,
    REJECT_KEYS,
    Gutter,
    MarkdownStream,
    Spinner,
    confirmation_request,
    random_thinking_text,
    render_plan,
    shimmer,
    usage_line,
)
from ui.components.terminal_approval import (
    ApprovalAction,
    TerminalApprovalPanel,
)
from ui.console import get_console
from ui.format import format_elapsed
from ui.motion import animations_enabled
from ui.panels import PanelOrchestrator, render_panel
from ui.theme import AGENT_THEME
from utils.paths import display_path_relative_to_cwd

MAX_EXPANSION_LINES = 24

PROGRESS_MAX_WIDTH = 48

APPROVAL_RISK_STYLES = {
    "normal": "info",
    "warn": "warning",
    "danger": "error",
}


class TUI:
    def __init__(self, config: Config, console: Console | None = None) -> None:
        self.console = console or get_console()
        self.config = config
        self.cwd = config.cwd
        self._assistant_stream_open = False
        self._assistant_markdown: MarkdownStream | None = None
        self._reasoning_stream_open = False
        self._reasoning_line = ""
        self._reasoning_started_at = 0.0
        self.panels = PanelOrchestrator()
        self._tool_progress = ""

        self._at_gap = True

        self.expanded = False
        self._spinner = Spinner(
            self.console,
            enabled=animations_enabled(config) and self.console.is_terminal,
        )
        self._thinking_label = ""
        self._thinking_started_at = 0.0
        self._turn_tokens = 0
        self._last_turn_tokens = 0
        self._context_tokens = 0
        self._context_state: str | None = None  # "ok" | "overflow" | "unavailable"
        self._plan_snapshot: dict[str, Any] = {}
        self._files_inspected: set[str] = set()
        self._files_modified: set[str] = set()
        self._patch_files_modified = 0
        self._tests_passed: tuple[int, int] | None = None
        self._agent_state = "idle"
        self._session_approved: set[str] = set()

        # Set by the REPL while it owns the keyboard, so confirmations can be
        # answered through its key reader instead of opening a second one.
        self.external_keys = False
        self._pending_confirmation: asyncio.Future[ApprovalAction] | None = None
        self._approval_panel: TerminalApprovalPanel | None = None
        self._approval_live: Live | None = None

    @property
    def approval_policy(self) -> ApprovalPolicy:
        return self.config.approval

    @property
    def execution_mode(self) -> ExecutionMode:
        return self.config.execution_mode

    @property
    def awaiting_confirmation(self) -> bool:
        return self._pending_confirmation is not None

    @property
    def context_ratio(self) -> float | None:
        window = self.config.effective_context_window
        if not window or not self._context_tokens:
            return None
        return min(self._context_tokens / window, 1.0)

    @property
    def turn_tokens(self) -> int | None:
        return self._turn_tokens or self._last_turn_tokens or None

    @property
    def context_tokens(self) -> int | None:
        return self._context_tokens or None

    @property
    def context_state(self) -> str | None:
        return self._context_state

    @property
    def agent_state(self) -> str:
        return self._agent_state

    def _set_agent_state(self, state: str) -> None:
        self._agent_state = state
        self.mark_dirty()

    def gap(self) -> None:
        if not self._at_gap:
            self.console.print()
            self._at_gap = True

    def print_block(self, *renderables: Any) -> None:
        for renderable in renderables:
            self.console.print(renderable)
        self._at_gap = False

    def mark_dirty(self) -> None:
        self._at_gap = False

    def _live_group(self, line: Text) -> Any:
        blocks: list[Any] = []
        plan = self.plan_view()
        if plan is not None:
            blocks.append(plan)
        expansion = self.expansion_renderable()
        if expansion is not None:
            blocks.append(expansion)
        blocks.append(line)
        return Group(*blocks)

    @property
    def plan_summary(self) -> str | None:
        steps = self._plan_snapshot.get("steps") or []
        if not steps:
            return None
        completed = self._plan_snapshot.get("completed", 0)
        total = self._plan_snapshot.get("total", len(steps))
        return f"PLAN {completed}/{total}"

    def plan_view(self) -> Any | None:
        if not self._plan_snapshot.get("steps"):
            return None
        return render_plan(self._plan_snapshot)

    def reset_plan_view(self) -> None:
        self._plan_snapshot = {}

    def reset_turn_summary(self) -> None:
        self._files_inspected.clear()
        self._files_modified.clear()
        self._patch_files_modified = 0
        self._tests_passed = None

    def reset_usage(self) -> None:
        """Clear context and turn usage measurements."""
        self._context_tokens = 0
        self._context_state = "unavailable"
        self._turn_tokens = 0
        self._last_turn_tokens = 0

    def render_completion_summary(
        self,
        completed: bool = True,
        interrupted: bool = False,
    ) -> None:
        """Show completion status and counters backed by completed tool results this turn.

        A task is complete only when the user's requested objective is verified.
        Files inspected, browser connected, or search results loaded do not mean completion.
        """
        if completed:
            status_text = Text("COMPLETED", style="success")
        elif interrupted:
            status_text = Text("INTERRUPTED", style="warning")
        else:
            status_text = Text("INCOMPLETE", style="warning")

        blocks: list[Any] = [status_text]
        if self._files_inspected:
            blocks.append(Text(f"Files inspected      {len(self._files_inspected)}", style="muted"))
        modified = len(self._files_modified) + self._patch_files_modified
        if modified:
            blocks.append(Text(f"Files modified       {modified}", style="muted"))
        if self._tests_passed is not None:
            passed, total = self._tests_passed
            blocks.append(Text(f"Tests passed         {passed}/{total}", style="muted"))
        self.gap()
        self.print_block(Group(*blocks))

    def _thinking_renderable(self) -> Any:
        line = Text.assemble((f"{self._spinner.char()} ", "tool"))
        line.append_text(shimmer(self._thinking_label, self._spinner.frame))
        elapsed = int(time.monotonic() - self._thinking_started_at)
        line.append(f" ({elapsed}s", style="muted")
        line.append(" | ", style="dim")
        line.append(f"{self._turn_tokens:,} tokens)", style="muted")
        return self._live_group(line)

    def update_turn_usage(
        self,
        usage: dict[str, Any] | None,
        turn_usage: dict[str, Any] | None = None,
    ) -> None:

        if not usage:
            return
        cumulative = turn_usage or usage.get("turn_usage") or usage
        self._turn_tokens = cumulative.get("total_tokens", 0) or 0
        if self._turn_tokens:
            self._last_turn_tokens = self._turn_tokens
        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        context_window = self.config.effective_context_window
        if not isinstance(prompt_tokens, (int, float)) or prompt_tokens < 0:
            # Invalid or stale usage event: do not reuse as current request.
            self._context_tokens = 0
            self._context_state = "unavailable"
        elif context_window and prompt_tokens > context_window:
            # Overflow: keep the raw value for display but flag the state.
            self._context_tokens = int(prompt_tokens)
            self._context_state = "overflow"
        else:
            self._context_tokens = int(prompt_tokens)
            self._context_state = "ok"

    def start_thinking(self, label: str | None = None) -> None:
        self._thinking_label = label if label is not None else random_thinking_text()

        if self._thinking_started_at == 0.0:
            self._thinking_started_at = time.monotonic()
        self._set_agent_state("thinking")
        self._spinner.start(self._thinking_renderable)

    def stop_thinking(self) -> None:
        self._spinner.stop()
        self._thinking_started_at = 0.0
        self._turn_tokens = 0
        self._set_agent_state("idle")

    @property
    def show_reasoning(self) -> bool:
        return self.config.reasoning.visible

    def _reasoning_renderable(self) -> Any:
        line = Text.assemble((f"{self._spinner.reasoning_char()} ", "reasoning.mark"))
        line.append_text(shimmer(REASONING_LABEL, self._spinner.frame))
        elapsed = int(time.monotonic() - self._reasoning_started_at)
        line.append(f" ({elapsed}s)", style="muted")
        return line

    def begin_reasoning(self) -> None:
        if not self.show_reasoning or self._reasoning_stream_open:
            return

        self._reasoning_stream_open = True
        self._reasoning_line = ""
        self._reasoning_started_at = time.monotonic()

        self.gap()
        self._spinner.start(self._reasoning_renderable)

    def stream_reasoning_delta(self, content: str) -> None:
        """Reasoning is printed a line at a time so the gutter can wrap with it."""

        if not self._reasoning_stream_open:
            return

        self._reasoning_line += content
        while "\n" in self._reasoning_line:
            line, self._reasoning_line = self._reasoning_line.split("\n", 1)
            self._print_reasoning_line(line)

    def end_reasoning(self) -> None:
        if not self._reasoning_stream_open:
            return

        self._spinner.stop()

        if self._reasoning_line.strip():
            self._print_reasoning_line(self._reasoning_line)
        self._reasoning_line = ""
        self._reasoning_stream_open = False

        elapsed = format_elapsed(time.monotonic() - self._reasoning_started_at)
        self.print_block(Text(f"  thought for {elapsed}", style="dim"))

    def _print_reasoning_line(self, line: str) -> None:
        self.print_block(
            Gutter(Text(line.rstrip(), style="reasoning"), style="reasoning.mark")
        )

    def begin_assistant(self) -> None:
        self._spinner.stop()
        self.gap()
        self._set_agent_state("responding")
        self._assistant_markdown = MarkdownStream(self._print_assistant_block)
        self._assistant_stream_open = True

    def end_assistant(self) -> None:
        if self._assistant_markdown is not None:
            self._assistant_markdown.close()
            self._assistant_markdown = None
        if self._assistant_stream_open:
            self.gap()
        self._assistant_stream_open = False
        self._set_agent_state("idle")

    def _print_assistant_block(self, renderable: Any) -> None:
        if isinstance(renderable, Text) and not renderable.plain:
            self.gap()
            return
        self.print_block(renderable)

    def stream_assistant_delta(self, content: str) -> None:
        if self._assistant_markdown is None:
            return
        self._assistant_markdown.feed(content)

    def _relativise(self, arguments: dict[str, Any]) -> dict[str, Any]:
        display_args = dict(arguments)
        for key in ("path", "cwd"):
            value = display_args.get(key)
            if isinstance(value, str) and self.cwd:
                display_args[key] = str(display_path_relative_to_cwd(value, self.cwd))
        return display_args

    def show_recent_tool(self, back: int = 1) -> None:

        panels = self.panels.panels
        if not panels or back < 1 or back > len(panels):
            self.print_block(Text("Nothing to expand.", style="muted"))
            return
        state = panels[-back]
        self.print_block(render_panel(state, expanded=True, width=self.console.width))

    def toggle_expansion(self) -> None:
        self.expanded = not self.expanded
        if self._spinner.running and self.panels.latest is not None:
            self._spinner.update(render_panel(
                self.panels.latest,
                expanded=self.expanded,
                width=self.console.width,
            ))

    def expansion_renderable(self, back: int = 1) -> Any | None:

        if not self.expanded or not self.panels.latest:
            return None
        panels = self.panels.panels
        if back < 1 or back > len(panels):
            return None
        state = panels[-back]
        if not state.details or state.name == "plan":
            return None
        return render_panel(state, expanded=True, width=self.console.width)

    def expansion_fragments(self, back: int = 1) -> StyleAndTextTuples:
        buffer = StringIO()
        console = Console(
            theme=AGENT_THEME,
            file=buffer,
            force_terminal=self.console.is_terminal,
            width=self.console.width,
            highlight=False,
        )
        plan = self.plan_view()
        details = self.expansion_renderable(back)
        if plan is not None:
            console.print(plan)
        if details is not None:
            console.print(details)
        if plan is None and details is None:
            return []

        lines = buffer.getvalue().rstrip("\n").split("\n")
        if len(lines) > MAX_EXPANSION_LINES:
            hidden = len(lines) - MAX_EXPANSION_LINES
            lines = lines[:MAX_EXPANSION_LINES] + [f"... {hidden} more lines"]
        return to_formatted_text(ANSI("\n".join(lines) + "\n"))

    def tool_call_start(
        self,
        call_id: str,
        name: str,
        tool_kind: str | None,
        arguments: dict[str, Any],
    ) -> None:
        display_args = self._relativise(arguments)
        state, created = self.panels.start(call_id, name, tool_kind, display_args)
        if not created:
            return
        self._tool_progress = ""
        self._set_agent_state("running")

        def render() -> Any:
            state.progress = self._tool_progress
            activity = render_panel(
                state, expanded=self.expanded, width=self.console.width
            )
            plan = self.plan_view()
            return Group(plan, activity) if plan is not None else activity

        if self.console.is_terminal:
            self._spinner.start(render, transient=True, live_static=True)

    def tool_progress(self, chunk: str) -> None:
        line = chunk.strip().splitlines()[-1] if chunk.strip() else ""
        if not line:
            return

        if len(line) > PROGRESS_MAX_WIDTH:
            line = f"{line[:PROGRESS_MAX_WIDTH - 1]}..."
        self._tool_progress = line
        state = self.panels.update_progress(line)
        if state is not None and self._spinner.running and not self._spinner.enabled:
            self._spinner.update(
                render_panel(state, width=self.console.width)
            )

    def tool_call_complete(
        self,
        call_id: str,
        name: str,
        tool_kind: str | None,
        success: bool,
        output: str,
        error: str | None,
        metadata: dict[str, Any] | None,
        truncated: bool,
        diff: str | None,
        exit_code: int | None,
    ) -> None:
        self._tool_progress = ""
        state, changed = self.panels.complete(
            call_id,
            success=success,
            output=output,
            error=error,
            metadata=metadata,
            truncated=truncated,
            diff=diff,
            exit_code=exit_code,
            model_name=self.config.model_name,
        )
        if not changed or state is None:
            return
        is_plan_update = name == "plan"
        if is_plan_update:
            self._plan_snapshot = dict(metadata or {})
        if success:
            target = state.arguments.get("path")
            if isinstance(target, str) and target:
                if name == "read":
                    self._files_inspected.add(target)
                elif name in {"write", "edit"}:
                    self._files_modified.add(target)
            patch_files = (metadata or {}).get("files") if name == "apply_patch" else None
            if isinstance(patch_files, int) and patch_files > 0:
                self._patch_files_modified += patch_files
            command = state.arguments.get("command", "")
            if name == "bash" and re.search(
                r"\b(pytest|unittest|vitest|jest|cargo\s+test|go\s+test|npm\s+test|test)\b",
                command,
                re.IGNORECASE,
            ):
                match = re.search(
                    r"(?P<passed>\d+)\s+passed(?:,\s*(?P<failed>\d+)\s+failed)?",
                    output,
                    re.IGNORECASE,
                )
                if match and not truncated:
                    passed = int(match.group("passed"))
                    failed_count = int(match.group("failed") or 0)
                    self._tests_passed = (passed, passed + failed_count)
        final_panel = render_panel(
            state,
            expanded=self.expanded,
            width=self.console.width,
        )
        if self._spinner.running:
            self._spinner.update(final_panel)
            self._spinner.stop()
        # Running panels are transient; retain exactly one completed panel in
        # the conversation after the live region is cleared.
        if not is_plan_update:
            self.print_block(final_panel)

    def render_confirmation_request(self, confirmation: ToolConfirmation) -> None:
        self.gap()
        self.print_block(*confirmation_request(confirmation, self.cwd))

    def cancel_active_panel(self) -> None:
        """Close the current panel cleanly when the user interrupts a turn."""
        call_id = self.panels.active_call_id
        if call_id is None:
            return
        state = self.panels.cancel(call_id)
        if state is None:
            return
        self._spinner.stop()
        self.print_block(render_panel(state, width=self.console.width))

    def approval_badge(self) -> str:
        policy = self.approval_policy
        return f"approval: {policy.label}"

    def execution_mode_badge(self) -> str:
        mode = self.execution_mode
        return f"mode: {mode.label}"

    def cycle_execution_mode(self) -> None:
        """Cycle to the next execution mode (Shift+Tab)."""
        modes = list(ExecutionMode)
        current = self.config.execution_mode
        current_idx = modes.index(current)
        next_idx = (current_idx + 1) % len(modes)
        self.config.execution_mode = modes[next_idx]
        self.mark_dirty()

    def render_execution_mode(self) -> None:
        mode = self.execution_mode
        line = Text.assemble(
            ("mode ", "muted"),
            (mode.label, "info"),
        )
        self.print_block(line)

    def cycle_approval_policy(self) -> None:
        """Cycle to the next approval policy (Shift+Tab)."""
        policies = list(ApprovalPolicy)
        current = self.config.approval
        current_idx = policies.index(current)
        next_idx = (current_idx + 1) % len(policies)
        self.config.approval = policies[next_idx]
        self.mark_dirty()

    def render_approval_mode(self) -> None:
        policy = self.approval_policy
        style = APPROVAL_RISK_STYLES.get(policy.risk, "info")
        line = Text.assemble(
            ("approval ", "muted"),
            (policy.label, style),
        )
        self.print_block(line)

    def feed_confirmation_key(self, key_press: Any) -> bool:
        """Answer a pending confirmation. Returns True when the key was consumed."""

        pending = self._pending_confirmation
        if pending is None:
            return False
        if pending.done():
            return True

        key = key_press.key
        data = (key_press.data or "").lower()

        panel = self._approval_panel
        if panel is not None and key in (Keys.Up, Keys.Down, Keys.ControlO):
            if key == Keys.Up:
                panel.move(-1)
            elif key == Keys.Down:
                panel.move(1)
            else:
                panel.toggle_details()
            if self._approval_live is not None:
                self._approval_live.update(panel, refresh=True)
        elif key in (Keys.ControlC, Keys.Escape):
            pending.set_result(ApprovalAction.CANCELLED)
        elif data in REJECT_KEYS:
            pending.set_result(ApprovalAction.REJECT)
        elif key in (Keys.ControlM, Keys.ControlJ):
            pending.set_result(panel.selected_action if panel is not None else ApprovalAction.ALLOW_ONCE)
        elif data in APPROVE_KEYS:
            if data == "y":
                pending.set_result(ApprovalAction.ALLOW_ONCE)
            elif data == "a":
                pending.set_result(ApprovalAction.ALLOW_SESSION)

        # Swallow everything while a confirmation is open; a stray ctrl+o
        # should not reflow the screen underneath the prompt.
        return True

    async def confirm_tool(self, confirmation: ToolConfirmation) -> bool:
        previous_spinner: Callable[[], Any] | None = self._spinner.render
        self._spinner.stop()

        # Preserve the existing per-tool/per-command session signature.
        signature = self._confirmation_signature(confirmation)
        if signature in self._session_approved:
            self.print_block(Text("Previously approved for this session", style="success"))
            if previous_spinner is not None:
                self._spinner.start(previous_spinner)
            return True

        self.panels.wait_for_approval()
        panel = TerminalApprovalPanel(confirmation, self.cwd)

        if self.external_keys:
            loop = asyncio.get_running_loop()
            pending: asyncio.Future[ApprovalAction] = loop.create_future()
            self._pending_confirmation = pending
            self._approval_panel = panel
            self.gap()
            try:
                with Live(
                    panel,
                    console=self.console,
                    transient=True,
                    auto_refresh=True,
                    refresh_per_second=4,
                ) as live:
                    self._approval_live = live
                    action = await pending
            except asyncio.CancelledError:
                self.print_block(Text("CANCELLED  No execution performed", style="warning"))
                raise
            except Exception:
                action = ApprovalAction.CANCELLED
            finally:
                self._approval_live = None
                self._approval_panel = None
                self._pending_confirmation = None
        else:
            action = await self._read_approval_interactive(panel)

        if action == ApprovalAction.ALLOW_SESSION:
            self._session_approved.add(signature)

        approved = action in {ApprovalAction.ALLOW_ONCE, ApprovalAction.ALLOW_SESSION}
        self.panels.resume_after_approval()

        if approved:
            label = "Allow once" if action == ApprovalAction.ALLOW_ONCE else "Allow for session"
            result = Text.assemble(("APPROVED  ", "success"), (label, "primary"))
        elif action == ApprovalAction.CANCELLED:
            result = Text("CANCELLED  No execution performed", style="warning")
        else:
            result = Text("REJECTED  Execution blocked", style="error")
        self.print_block(result)

        if previous_spinner is not None:
            self._spinner.start(previous_spinner)
        return approved

    def _confirmation_signature(self, confirmation: ToolConfirmation) -> str:
        """Stable signature for a confirmation, used for session approval tracking."""
        parts = [confirmation.tool_name, confirmation.command or ""]
        return "|".join(parts)

    async def _read_approval_interactive(self, panel: TerminalApprovalPanel) -> ApprovalAction:
        """Run the shared keyboard-first approval selector; fail closed without a TTY."""
        from prompt_toolkit.input import create_input

        try:
            device = create_input()
        except Exception:
            self.console.print(Text("No terminal available; operation cancelled.", style="warning"))
            return ApprovalAction.CANCELLED

        loop = asyncio.get_running_loop()
        key_queue: asyncio.Queue[Any] = asyncio.Queue()

        def on_keys() -> None:
            try:
                for key_press in device.read_keys():
                    loop.call_soon_threadsafe(key_queue.put_nowait, key_press)
            except Exception:
                pass

        try:
            with Live(
                panel,
                console=self.console,
                transient=True,
                auto_refresh=True,
                refresh_per_second=4,
            ) as live:
                with device.raw_mode(), device.attach(on_keys):
                    while True:
                        key_press = await key_queue.get()
                        key = key_press.key
                        data = (key_press.data or "").lower()

                        if key == Keys.Up:
                            panel.move(-1)
                            live.update(panel, refresh=True)
                        elif key == Keys.Down:
                            panel.move(1)
                            live.update(panel, refresh=True)
                        elif key == Keys.ControlO:
                            panel.toggle_details()
                            live.update(panel, refresh=True)
                        elif key in (Keys.ControlM, Keys.ControlJ):
                            return panel.selected_action
                        elif key in (Keys.ControlC, Keys.Escape):
                            return ApprovalAction.CANCELLED
                        elif data == "y":
                            return ApprovalAction.ALLOW_ONCE
                        elif data == "a":
                            return ApprovalAction.ALLOW_SESSION
                        elif data == "n":
                            return ApprovalAction.REJECT
        except Exception:
            return ApprovalAction.CANCELLED

    def render_usage(self, usage: dict[str, Any] | None) -> None:
        if not usage:
            return

        prompt_tokens = usage.get("prompt_tokens", 0) or 0
        context_window = self.config.effective_context_window
        if not isinstance(prompt_tokens, (int, float)) or prompt_tokens < 0:
            self._context_tokens = 0
            self._context_state = "unavailable"
        elif context_window and prompt_tokens > context_window:
            self._context_tokens = int(prompt_tokens)
            self._context_state = "overflow"
        else:
            self._context_tokens = int(prompt_tokens)
            self._context_state = "ok"

        self.gap()
        self.print_block(usage_line(usage, context_window))

    def render_context_usage(self, usage: dict[str, Any] | None = None) -> None:
        """Display context usage: used, capacity, remaining, input/output/cached tokens."""
        context_window = self.config.effective_context_window
        reported_tokens = (usage.get("prompt_tokens", 0) if usage else 0) or 0
        context_tokens = self._context_tokens or (
            reported_tokens if reported_tokens <= context_window else 0
        )
        turn_tokens = self._turn_tokens or 0
        context_state = self._context_state

        if not context_window:
            return

        if context_state == "overflow":
            used_ratio = 1.0
            remaining = 0
            style = "error"
            label = "OVERFLOW"
        elif context_state == "unavailable":
            return
        else:
            used_ratio = min(context_tokens / context_window, 1.0)
            remaining = max(0, context_window - context_tokens)

            # Determine style based on usage level.
            if used_ratio >= 0.9:
                style = "error"
                label = "CRITICAL"
            elif used_ratio >= 0.7:
                style = "warning"
                label = "HIGH"
            else:
                style = "info"
                label = "OK"

        line = Text.assemble(
            ("context ", "muted"),
            (f"{context_tokens:,}", style),
            (f" / {context_window:,} ", "muted"),
            (f"({remaining:,} remaining)", "dim"),
            (f"  |  turn: {turn_tokens:,}", "muted"),
            (f"  |  [{label}]", style),
        )
        self.print_block(line)
