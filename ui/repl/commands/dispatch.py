from __future__ import annotations

import asyncio
from typing import Callable

from rich.console import Console
from rich.text import Text

from agent.agent import Agent
from config.config import Config
from ui.repl.commands.base import CommandGroup
from ui.repl.commands.help import HelpCommands
from ui.repl.commands.inspect import InspectCommands
from ui.repl.commands.sessions import SessionCommands
from ui.repl.commands.settings import SettingsCommands

Handler = Callable[[Agent, list[str]], None | object]

EXIT_NAMES = {"exit", "quit"}

COMMAND_HINTS: dict[str, str] = {
    "help": "Show this help",
    "clear": "Clear conversation history",
    "config": "Show current configuration",
    "model": "Change the model",
    "thinking": "Configure model reasoning",
    "reasoning": "Alias for /thinking",
    "stats": "Show session statistics",
    "tools": "List available tools",
    "mcp": "Show MCP server status",
    "sessions": "List saved sessions",
    "resume": "Load a saved session",
    "checkpoint": "Save a checkpoint now",
    "save": "Alias for /checkpoint",
    "checkpoints": "List checkpoints in this session",
    "rewind": "Roll the conversation back",
    "compact": "Compact conversation context",
    "mode": "Show or set execution mode (/auto, /manual, /plan, /accept-edits)",
    "auto": "Set execution mode to AUTO",
    "manual": "Set execution mode to MANUAL",
    "plan": "Set execution mode to PLAN",
    "accept-edits": "Set execution mode to ACCEPT EDITS",
    "approval": "Show or set approval policy (on_request, on_fail, auto, auto_edit, never, yolo)",
    "setup": "Run the setup wizard to configure a provider",
    "providers": "List available AI providers",
    "doctor": "Run diagnostics and health check",
    "browser": "KRYTH Browser commands (setup, doctor, status, tabs, reconnect, uninstall)",
    "exit": "Exit the agent",
    "quit": "Exit the agent",
}


class SlashCommands(HelpCommands, SettingsCommands, SessionCommands, InspectCommands):
    """Everything typed after a `/`, routed to the group that handles it."""

    def __init__(self, config: Config, console: Console, tui=None) -> None:
        CommandGroup.__init__(self, config, console)
        self.tui = tui

        self._handlers: dict[str, Handler] = {
            "help": self.show_help,
            "clear": self.clear_history,
            "config": self.show_config,
            "model": self.set_model,
            "thinking": self.set_thinking,
            "reasoning": self.set_thinking,
            "stats": self.show_stats,
            "tools": self.show_tools,
            "mcp": self.show_mcp,
            "sessions": self.list_sessions,
            "resume": self.resume_session,
            "checkpoint": self.save_checkpoint,
            "save": self.save_checkpoint,
            "checkpoints": self.list_checkpoints,
            "rewind": self.rewind,
            "compact": self.compact_context,
            "mode": self.mode_command,
            "auto": self.mode_command,
            "manual": self.mode_command,
            "plan": self.mode_command,
            "accept-edits": self.mode_command,
            "approval": self.approval_command,
            "setup": self.run_setup,
            "providers": self.list_providers,
            "doctor": self.run_doctor,
            "browser": self.browser_command,
        }

    def command_names(self) -> list[str]:
        return sorted(self._handlers.keys() | EXIT_NAMES)

    def describe(self, name: str) -> str:
        return COMMAND_HINTS.get(name, "")

    def is_command(self, message: str) -> bool:
        return message.startswith("/")

    async def execute(self, agent: Agent, message: str) -> bool:
        """Run one command. Returns False when the REPL should stop."""

        parts = message.split()
        name = parts[0].lstrip("/").lower()
        args = parts[1:]

        if name in EXIT_NAMES:
            return False

        handler = self._handlers.get(name)
        if handler is None:
            self.console.print(f"[error]Unknown command: /{name}[/error]")
            self.console.print(Text("Type /help to see available commands.", style="muted"))
            return True

        result = handler(agent, args)
        if asyncio.iscoroutine(result):
            await result
        return True

    async def run_setup(self, agent: Agent, args: list[str]) -> None:
        """Run the setup wizard to configure a provider."""
        from ui.setup_tui import SetupTUI

        setup = SetupTUI(self.config, self.console)
        result = await setup.run()
        if result.success:
            self.console.print(
                f"[success]Setup complete: {result.provider} ({result.model})[/success]"
            )
        else:
            self.console.print(f"[warning]Setup cancelled: {result.message}[/warning]")

    async def list_providers(self, agent: Agent, args: list[str]) -> None:
        """List available AI providers."""
        from config.setup_wizard import SetupWizard

        wizard = SetupWizard(self.config)
        providers = wizard.list_known_providers()

        self.console.print()
        self.console.print(Text("Available providers:", style="bold primary"))
        for i, p in enumerate(providers, 1):
            defaults = wizard.select_provider_defaults(p)
            ptype = defaults.get("type", "openai")
            marker = " (active)" if p == self.config.providers.active_name else ""
            self.console.print(f"  {i}. {p} ({ptype}){marker}", style="muted")
        self.console.print()

    async def list_models(self, agent: Agent, args: list[str]) -> None:
        """List available models for the current provider."""
        from config.setup_wizard import SetupWizard
        from ui.setup_tui import SetupTUI

        provider = self.config.providers.active_name
        if not provider:
            self.console.print("[error]No provider configured. Run /setup first.[/error]")
            return

        wizard = SetupWizard(self.config)
        api_key = self.config.api_key or ""
        base_url = self.config.base_url or ""

        self.console.print()
        self.console.print(f"Fetching models for {provider}...", style="bold")

        try:
            models = await wizard.fetch_models(provider, base_url, api_key)
        except Exception as e:
            self.console.print(f"[error]Failed to fetch models: {e}[/error]")
            return

        if not models:
            self.console.print(
                "[warning]No models discovered. You can still set a model manually with /model <name>.[/warning]"
            )
            return

        self.console.print(Text(f"Available models for {provider}:", style="bold primary"))
        for i, m in enumerate(models[:50], 1):
            marker = " (current)" if m == self.config.model_name else ""
            self.console.print(f"  {i}. {m}{marker}", style="muted")
        if len(models) > 50:
            self.console.print(f"  … and {len(models) - 50} more", style="muted")
        self.console.print()

    async def run_doctor(self, agent: Agent, args: list[str]) -> None:
        """Run diagnostics and health check."""
        from rich.table import Table

        self.console.print()
        self.console.print(Text("KRYTH Doctor — System Diagnostics", style="bold primary"))
        self.console.print()

        table = Table(show_header=False, box=None)
        table.add_column(style="muted")
        table.add_column()

        # Config directory
        from config.loader import get_config_dir
        config_dir = get_config_dir()
        table.add_row("Config dir", str(config_dir))
        table.add_row("Config exists", "yes" if config_dir.is_dir() else "no")

        # Credentials
        from config.keyring_store import list_stored_providers
        stored = list_stored_providers()
        table.add_row("Stored providers", ", ".join(stored) if stored else "none")

        # Active provider
        provider = self.config.providers.active_name
        table.add_row("Active provider", provider or "none")
        table.add_row("Model", self.config.model_name or "none")

        # API key
        api_key = self.config.api_key
        table.add_row("API key", "set" if api_key else "missing")

        # Base URL
        base_url = self.config.base_url
        table.add_row("Base URL", base_url or "none")

        self.console.print(table)

        # Config validation
        errors = self.config.validate()
        if errors:
            self.console.print()
            self.console.print(Text("Configuration issues:", style="bold red"))
            for error in errors:
                self.console.print(f"  - {error}", style="red")
        else:
            self.console.print()
            self.console.print("[success]Configuration looks good![/success]")

        self.console.print()

    async def browser_command(self, agent: Agent, args: list[str]) -> None:
        """KRYTH Browser subcommands: setup, doctor, status, tabs, reconnect, uninstall.

        Usage: /browser setup        Run first-run setup
               /browser doctor       Check browser and relay readiness
               /browser status       Show relay/extension status
               /browser tabs         List open browser tabs
               /browser reconnect    Restart the relay
               /browser uninstall    Stop the relay and clear integration
        """
        from kryth_browser.cli_commands import (
            run_browser_setup,
            run_browser_doctor,
            run_browser_status,
            run_browser_tabs,
            run_browser_reconnect,
            run_browser_uninstall,
        )

        if not args:
            self.console.print(
                "[error]Usage: /browser <setup|doctor|status|tabs|reconnect|uninstall>[/error]"
            )
            return

        sub = args[0].lower()
        rest = args[1:]

        try:
            if sub == "setup":
                await run_browser_setup(self.console, rest)
            elif sub == "doctor":
                await run_browser_doctor(self.console, rest)
            elif sub == "status":
                await run_browser_status(self.console, rest)
            elif sub == "tabs":
                await run_browser_tabs(self.console, rest)
            elif sub == "reconnect":
                await run_browser_reconnect(self.console, rest)
            elif sub == "uninstall":
                await run_browser_uninstall(self.console, rest)
            else:
                self.console.print(f"[error]Unknown browser subcommand: /browser {sub}[/error]")
        except Exception as exc:  # noqa: BLE001
            self.console.print(f"[error]Browser command failed: {exc}[/error]")

    async def compact_context(self, agent: Agent, args: list[str]) -> None:
        """Compact conversation context into a structured continuation summary.

        Usage: /compact [additional preservation instructions]
        """
        from rich.panel import Panel
        from rich.text import Text

        from context.compaction import ChatCompactor

        session = agent.session
        context_manager = session.context_manager

        if context_manager is None:
            self.console.print(
                "[error]Context manager not initialized.[/error]"
            )
            return

        if context_manager.message_count == 0:
            self.console.print(
                Text("Nothing to compact — conversation is empty.", style="muted")
            )
            return

        # Parse optional preservation instructions.
        preservation_instructions = " ".join(args).strip() or None

        # Gather task snapshot from the development supervisor.
        ds = session.development_supervisor
        task_snapshot = ds.state.get("task_checkpoint") or {}

        # Show progress panel.
        self.console.print()
        self.console.print(
            Panel(
                Text("KRYTH CONTEXT MANAGER", style="bold primary"),
                border_style="primary",
            )
        )

        # Show progress steps.
        steps = [
            "Analyzing conversation",
            "Preserving task state",
            "Generating continuation summary",
            "Rebuilding context",
            "Verifying new context budget",
        ]
        for step in steps:
            self.console.print(f"  {step}...", style="muted")

        # Estimate previous context.
        from utils.text import estimate_request_tokens

        previous_messages = context_manager.get_messages()
        previous_estimate = estimate_request_tokens(
            previous_messages,
            None,
            model=self.config.model_name,
            tokenizer_encoding=self.config.effective_tokenizer_encoding,
        )
        context_window = self.config.effective_context_window
        previous_utilization = (
            previous_estimate / context_window if context_window else 0.0
        )

        # Run compaction.
        compactor = ChatCompactor(session.client, self.config)
        result = await compactor.compact(
            context_manager,
            preservation_instructions=preservation_instructions,
            task_snapshot=task_snapshot if task_snapshot.get("task_id") else None,
        )

        # Display results.
        self.console.print()
        if result.succeeded:
            self.console.print(
                Text(
                    f"Context compacted: {previous_utilization:.1%} → "
                    f"{result.new_utilization:.1%}",
                    style="success",
                )
            )
            self.console.print(
                Text(
                    f"Previous estimate: {result.previous_estimate:,} tokens · "
                    f"New estimate: {result.new_estimate:,} tokens",
                    style="muted",
                )
            )
            self.console.print(
                Text(
                    f"Messages compacted: {result.messages_compacted} · "
                    f"Messages preserved: {result.messages_preserved}",
                    style="muted",
                )
            )
            if result.task_checkpoint and result.task_checkpoint.get("task_id"):
                status = result.task_checkpoint.get("status", "unknown")
                self.console.print(
                    Text(
                        f"Task checkpoint: {result.task_checkpoint['task_id']} "
                        f"({status})",
                        style="muted",
                    )
                )
            self.console.print(
                Text("Compaction succeeded. Resuming current task...", style="success")
            )
        else:
            self.console.print(
                Text(f"Compaction failed: {result.error}", style="error")
            )

        self.console.print()

    def mode_command(self, agent: Agent, args: list[str]) -> None:
        """Show or set the execution mode.

        Usage: /mode              - show current mode
               /mode auto         - set AUTO mode
               /mode manual       - set MANUAL mode
               /mode plan         - set PLAN mode
               /mode accept-edits - set ACCEPT EDITS mode
        """
        from config.config import ExecutionMode

        mode_map: dict[str, ExecutionMode] = {
            "auto": ExecutionMode.AUTO,
            "manual": ExecutionMode.MANUAL,
            "plan": ExecutionMode.PLAN,
            "accept-edits": ExecutionMode.ACCEPT_EDITS,
            "accept_edits": ExecutionMode.ACCEPT_EDITS,
        }

        if not args:
            mode = self.config.execution_mode
            self.console.print(
                f"[muted]Execution mode: {mode.label} — {mode.summary}[/muted]"
            )
            self.console.print(
                "[muted]Use /mode <auto|manual|plan|accept-edits> to change, "
                "or Shift+Tab to cycle.[/muted]"
            )
            return

        name = args[0].lower()
        if name not in mode_map:
            self.console.print(f"[error]Unknown mode: {name}[/error]")
            self.console.print(
                "[muted]Available: auto, manual, plan, accept-edits[/muted]"
            )
            return

        self.config.execution_mode = mode_map[name]
        mode = self.config.execution_mode
        self.console.print(
            f"[success]Execution mode set to {mode.label}.[/success] "
            f"[muted]{mode.summary}[/muted]"
        )

    def approval_command(self, agent: Agent, args: list[str]) -> None:
        """Show or set the approval policy.

        Usage: /approval                    - show current policy
               /approval on_request         - confirm every mutating tool
               /approval on_fail            - ask only after a failure
               /approval auto               - approve everything except dangerous
               /approval auto_edit          - edit freely, confirm bash commands
               /approval never              - reject anything not known safe
               /approval yolo               - approve everything, including dangerous
        """
        from config.config import ApprovalPolicy

        policy_map: dict[str, ApprovalPolicy] = {
            "on_request": ApprovalPolicy.ON_REQUEST,
            "on_fail": ApprovalPolicy.ON_FAIL,
            "auto": ApprovalPolicy.AUTO,
            "auto_edit": ApprovalPolicy.AUTO_EDIT,
            "never": ApprovalPolicy.NEVER,
            "yolo": ApprovalPolicy.YOLO,
        }

        if not args:
            policy = self.config.approval
            self.console.print(
                f"[muted]Approval policy: {policy.label} — {policy.summary}[/muted]"
            )
            self.console.print(
                "[muted]Use /approval <on_request|on_fail|auto|auto_edit|never|yolo> "
                "to change.[/muted]"
            )
            return

        name = args[0].lower()
        if name not in policy_map:
            self.console.print(f"[error]Unknown policy: {name}[/error]")
            self.console.print(
                "[muted]Available: on_request, on_fail, auto, auto_edit, never, yolo[/muted]"
            )
            return

        self.config.approval = policy_map[name]
        policy = self.config.approval
        self.console.print(
            f"[success]Approval policy set to {policy.label}.[/success] "
            f"[muted]{policy.summary}[/muted]"
        )
