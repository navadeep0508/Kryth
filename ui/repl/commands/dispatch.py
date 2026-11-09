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
    "setup": "Run the setup wizard to configure a provider",
    "providers": "List available AI providers",
    "doctor": "Run diagnostics and health check",
    "exit": "Exit the agent",
    "quit": "Exit the agent",
}


class SlashCommands(HelpCommands, SettingsCommands, SessionCommands, InspectCommands):
    """Everything typed after a `/`, routed to the group that handles it."""

    def __init__(self, config: Config, console: Console) -> None:
        CommandGroup.__init__(self, config, console)

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
            "setup": self.run_setup,
            "providers": self.list_providers,
            "doctor": self.run_doctor,
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
        self.console.print(Text("Available providers:", style="bold #E8FF3A"))
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

        self.console.print(Text(f"Available models for {provider}:", style="bold #E8FF3A"))
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
        self.console.print(Text("KRYTH Doctor — System Diagnostics", style="bold #E8FF3A"))
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
