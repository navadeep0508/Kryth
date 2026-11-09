from __future__ import annotations

import asyncio

from pathlib import Path

from platformdirs import user_config_dir
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import FileHistory
from rich.text import Text

from agent.agent import Agent
from config.config import Config
from config.setup_wizard import SetupWizard
from ui.components import echo_user_message
from ui.components.notification import notification
from ui.console import get_console
from ui.repl.banner import render_banner
from ui.repl.commands import SlashCommands
from ui.repl.keys import build_key_bindings, turn_keys
from ui.repl.prompt import (
    PROMPT_STYLE,
    bottom_fragments,
    continuation_fragments,
    prompt_fragments,
    status_readout,
)
from ui.setup_tui import SetupTUI
from ui.stream import stream_turn
from ui.tui import TUI

HISTORY_FILE = "history"


def _history_path() -> Path:
    path = Path(user_config_dir("kryth")) / HISTORY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class Repl:

    def __init__(
        self,
        config: Config,
        resume: str | None = None,
        upgrade_notice: str | None = None,
    ) -> None:
        self.config = config
        self.resume = resume
        self.upgrade_notice = upgrade_notice
        self.console = get_console()
        self.tui = TUI(config, self.console)
        self.commands = SlashCommands(config, self.console)
        self._completion_index = 0
        self._completion_scroll = 0
        self.session: PromptSession[str] = PromptSession(
            history=FileHistory(str(_history_path())),
            style=PROMPT_STYLE,
            key_bindings=build_key_bindings(self.tui, self),
            erase_when_done=True,
        )
        self.git_branch: str | None = None

    def _banner(self, agent: Agent) -> None:
        render_banner(
            self.console,
            self.config,
            agent.session.short_id,
            self.git_branch,
            fresh=not agent.session.turns and agent.resumed is None,
            last_task=(
                agent.session.title
                if agent.resumed is not None and agent.session.title != "(untitled)"
                else None
            ),
        )
        if self.upgrade_notice:
            self.tui.print_block(
                notification(
                    self.upgrade_notice,
                    label="Update available",
                    tone="warning",
                    width=self.console.width,
                )
            )
        self.tui.mark_dirty()

    def _farewell(self, agent: Agent) -> None:
        session = agent.session

        # A turn cut short by ctrl+c never reached its autosave.
        if session.turns:
            session.save_checkpoint()

        line = Text.assemble(("Session ", "muted"), (session.session_id, "subtitle"))

        self.tui.gap()
        self.tui.print_block(line)

        if self.config.session.enabled and session.store.exists(session.session_id):
            self.tui.print_block(
                Text(f"Resume it with kryth --resume {session.short_id}", style="border")
            )
        else:
            self.tui.print_block(Text("Keep this id to resume the session.", style="border"))

    def _reset_plan(self, agent: Agent) -> None:
        tool = agent.session.tool_registry.get("plan")
        if tool is not None and hasattr(tool, "reset"):
            tool.reset()

    async def _run_turn(self, agent: Agent, message: str) -> None:
        task = asyncio.create_task(stream_turn(self.tui, agent, message))

        try:
            with turn_keys(self.tui, task):
                await task
        except asyncio.CancelledError:
            self.tui.stop_thinking()
            self.tui.gap()
            self.tui.print_block(Text("Interrupted, what should kryth do instead?", style="warning"))
        except Exception as exc:
            self.tui.stop_thinking()
            self.tui.gap()
            self.tui.print_block(Text(f"{type(exc).__name__}: {exc}", style="error"))

    def _prompt_fragments(self) -> StyleAndTextTuples:
        text = self.session.default_buffer.text
        expansion = self.tui.expansion_fragments()

        if text.startswith("/") and " " not in text:
            prefix = text[1:]
            matches = [
                (name, self.commands.describe(name))
                for name in self.commands.command_names()
                if name.startswith(prefix)
            ]
            if matches:
                # Reset scroll when the selection wraps past the visible window.
                max_show = 8
                if self._completion_index >= len(matches):
                    self._completion_index = 0
                    self._completion_scroll = 0

                cmd_fragments: StyleAndTextTuples = []

                # Keep the selected item inside the scroll window.
                if self._completion_index < self._completion_scroll:
                    self._completion_scroll = self._completion_index
                elif self._completion_index >= self._completion_scroll + max_show:
                    self._completion_scroll = self._completion_index - max_show + 1
                self._completion_scroll = max(
                    0, min(self._completion_scroll, max(0, len(matches) - max_show))
                )

                visible = matches[
                    self._completion_scroll : self._completion_scroll + max_show
                ]
                for idx, (name, desc) in enumerate(visible):
                    actual_idx = self._completion_scroll + idx
                    is_selected = actual_idx == self._completion_index
                    mark = "  > " if is_selected else "    "
                    cmd_str = f"/{name}".ljust(18)

                    cmd_style = "class:prompt bold" if is_selected else "class:frame"
                    meta_style = "class:prompt" if is_selected else "class:status"

                    cmd_fragments.append(("", mark))
                    cmd_fragments.append((cmd_style, cmd_str))
                    cmd_fragments.append((meta_style, f"  {desc}\n"))

                if len(matches) > max_show:
                    lo = self._completion_scroll + 1
                    hi = min(self._completion_scroll + max_show, len(matches))
                    cmd_fragments.append(
                        ("class:status", f"    … showing {lo}-{hi} of {len(matches)}\n")
                    )

                expansion = (expansion or []) + cmd_fragments

        return prompt_fragments(
            self.console.width,
            expansion,
            placeholder=not text,
        )

    def _continuation_fragments(
        self, width: int, _line_number: int, _wrap_count: int
    ) -> StyleAndTextTuples:
        return continuation_fragments(width)

    def _bottom_fragments(self) -> StyleAndTextTuples:
        return bottom_fragments(
            self.console.width,
            self.tui.approval_badge(),
            self.config.approval.risk,
            status_readout(
                self.config.model_name,
                self.tui.context_ratio,
                self.console.width,
                self.config.approval.label,
                str(self.config.cwd),
                provider=self.config.providers.active_name,
                agent_state=self.tui.agent_state,
                turn_tokens=self.tui.turn_tokens,
                git_branch=self.git_branch,
            ),
        )

    async def _read_input(self) -> str:
        self.tui.gap()
        try:
            message = await self.session.prompt_async(
                self._prompt_fragments,
                bottom_toolbar=self._bottom_fragments,
                prompt_continuation=self._continuation_fragments,
            )
            return message
        finally:
            self.tui.expanded = False

    def _echo(self, message: str) -> None:
        self.tui.gap()
        self.tui.print_block(echo_user_message(message))

    def _resume_notice(self, agent: Agent) -> None:
        if agent.resumed is not None:
            self.commands.announce_resume(agent)
            self.tui.mark_dirty()
            return

        self.tui.gap()
        self.tui.print_block(
            Text(f"No saved session matched '{self.resume}', starting a new one.", style="warning")
        )

    async def run(self) -> None:
        async with Agent(
            self.config,
            confirmation_callback=self.tui.confirm_tool,
            progress_callback=self.tui.tool_progress,
            resume=self.resume,
        ) as agent:
            context = agent.session.project_context
            self.git_branch = context.git_branch if context else None
            # First-launch detection: run setup wizard when no provider is configured.
            if SetupWizard(self.config).is_first_launch():
                self.console.print(
                    Text("No provider configured — let's set one up.", style="bold")
                )
                setup = SetupTUI(self.config, self.console)
                result = await setup.run()
                if not result.success:
                    self.console.print(
                        Text(
                            "Setup cancelled. Some features will be unavailable until you run /setup.",
                            style="warning",
                        )
                    )

            self._banner(agent)
            if self.resume:
                self._resume_notice(agent)
            try:
                while True:
                    try:
                        message = await self._read_input()
                    except KeyboardInterrupt:
                        continue
                    except EOFError:
                        break

                    message = message.strip()
                    if not message:
                        continue

                    self._echo(message)

                    if self.commands.is_command(message):
                        self.tui.gap()
                        keep_going = await self.commands.execute(agent, message)
                        self.tui.mark_dirty()
                        if not keep_going:
                            break
                        continue

                    self._reset_plan(agent)
                    await self._run_turn(agent, message)
            finally:
                self._farewell(agent)
