"""Polished setup TUI for KRYTH first-launch onboarding.

Rich panels with Acid Gold (#E8FF3A) branding, prompt_toolkit inputs with
tab completion and masked API key entry, model discovery via the provider's
/models endpoint, and a spinner animation during model loading and
connection testing. Designed to be launched both from the CLI (`kryth setup`)
and from within the TUI (`/setup`).
"""

from __future__ import annotations

import asyncio

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich import box

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from config.config import Config
from config.providers import ProviderType
from config.setup_wizard import SetupWizard, WizardResult
from ui.components.spinner import Spinner
from ui.theme.palette import hex_colour

KRYTH_GOLD = hex_colour("accent")

# Local providers that do not require an API key.
LOCAL_PROVIDERS = {"ollama", "lmstudio"}


class SetupTUI:
    """Interactive setup wizard with polished Rich + prompt_toolkit screens."""

    def __init__(self, config, console: Console | None = None) -> None:
        self.config = config
        self.console = console or Console()
        self.wizard = SetupWizard(config)

    # -- public API -----------------------------------------------------

    async def run(self) -> WizardResult:
        """Run the full setup wizard. Returns a WizardResult."""

        try:
            self._show_welcome()

            # Step 1: Provider
            provider = await self._pick_provider()
            if provider is None:
                return WizardResult(success=False, message="Setup cancelled: no provider selected")

            # Step 2: Base URL (only for custom providers)
            defaults = self.wizard.select_provider_defaults(provider)
            base_url = defaults.get("base_url", "")
            if provider == "custom":
                self.console.print("Base URL for custom provider:", style="bold")
                self.console.print()
                session = PromptSession()
                base_url = (await session.prompt_async("Base URL > ")).strip()
                if not base_url:
                    return WizardResult(success=False, message="Setup cancelled: no base URL entered")

            # Step 3: API key
            api_key = await self._ask_api_key(provider)
            if api_key is None and provider not in LOCAL_PROVIDERS:
                return WizardResult(success=False, message="Setup cancelled: no API key entered")

            # Step 4: Model discovery
            models = await self._fetch_models_with_spinner(provider, base_url, api_key)

            # Step 5: Model selection
            model = await self._select_model(models, provider)
            if model is None:
                return WizardResult(success=False, message="Setup cancelled: no model selected")

            # Save
            self.wizard.save_setup(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
            )

            self._show_complete(provider, model)
            return WizardResult(
                success=True,
                provider=provider,
                model=model,
                base_url=base_url,
                message="Setup complete",
            )
        except (KeyboardInterrupt, EOFError):
            self.console.print()
            return WizardResult(success=False, message="Setup cancelled")

    # -- screens ----------------------------------------------------------

    def _show_welcome(self) -> None:
        """Render the welcome banner."""
        self.console.print()
        self.console.print(
            Panel(
                Group(
                    Text("KRYTH", style=f"bold {KRYTH_GOLD}", justify="center"),
                    Text("Set up your AI provider", style="muted", justify="center"),
                    Text(
                        "Configure your API key, model, and connection in under a minute.",
                        style="dim",
                        justify="center",
                    ),
                ),
                box=box.ROUNDED,
                border_style=KRYTH_GOLD,
                padding=(1, 4),
            )
        )
        self.console.print()

    async def _pick_provider(self) -> str | None:
        """Step 1: choose a provider with arrow-key (↑/↓) navigation."""
        known = self.wizard.list_known_providers()
        state = {"selected": 0}

        def get_text():
            result = []
            for i, p in enumerate(known):
                defaults = self.wizard.select_provider_defaults(p)
                ptype = defaults.get("type", ProviderType.OPENAI)
                if i == state["selected"]:
                    result.append(("bold #E8FF3A reverse", f"  ▸ {p} ({ptype.value})\n"))
                else:
                    result.append(("", f"    {p} ({ptype.value})\n"))
            result.append(
                ("class:msg", "\n↑/↓ navigate  •  Enter select  •  Esc cancel\n")
            )
            return result

        kb = KeyBindings()

        @kb.add("up")
        def _(event):
            state["selected"] = max(0, state["selected"] - 1)

        @kb.add("down")
        def _(event):
            state["selected"] = min(len(known) - 1, state["selected"] + 1)

        @kb.add("enter")
        def _(event):
            event.app.exit(result=known[state["selected"]])

        @kb.add("c-c")
        @kb.add("escape")
        def _(event):
            event.app.exit(result=None)

        layout = Layout(
            HSplit([
                Window(height=1, content=FormattedTextControl(
                    lambda: [("bold", "[1/4] Select your AI provider:\n")])),
                Window(content=FormattedTextControl(get_text)),
            ])
        )

        app = Application(layout=layout, key_bindings=kb, full_screen=True)

        try:
            result = await app.run_async()
        except Exception:
            result = None

        return result if result else None

    async def _ask_api_key(self, provider: str) -> str | None:
        """Step 3: masked API key entry (skipped for local providers)."""
        if provider in LOCAL_PROVIDERS:
            self.console.print(f"API key for {provider}:", style="bold")
            self.console.print("  Local provider — API key not required", style="muted")
            self.console.print()
            return ""

        self.console.print(f"API key for {provider}:", style="bold")
        self.console.print("  Input will be hidden as you type", style="muted")
        self.console.print()

        session = PromptSession()
        api_key = (await session.prompt_async("API key > ", is_password=True)).strip()
        if not api_key:
            return None
        return api_key

    async def _fetch_models_with_spinner(
        self, provider: str, base_url: str, api_key: str
    ) -> list[str]:
        """Step 4: fetch available models from the provider with a spinner."""
        spinner = Spinner(self.console)

        def render() -> Text:
            return Text(f"{spinner.char()}  Loading available models...", style=KRYTH_GOLD)

        spinner.start(render)
        try:
            models = await self.wizard.fetch_models(provider, base_url, api_key)
        finally:
            spinner.stop()

        if models:
            self.console.print(f"  [ok] Found {len(models)} models", style="success")
        else:
            self.console.print(
                f"  [warn] Could not discover models — you can enter one manually",
                style="warning",
            )
        self.console.print()
        return models

    async def select_model_for_provider(self, provider: str) -> str | None:
        """Fetch models for a provider and let the user pick one interactively.

        Used by /model to browse and select from the provider's available models.
        """
        api_key = self.config.api_key or ""
        base_url = self.config.base_url or ""

        models = await self._fetch_models_with_spinner(provider, base_url, api_key)
        return await self._select_model(models, provider)

    async def _select_model(self, models: list[str], provider: str) -> str | None:
        """Step 5: choose a model from the discovered list with arrow-key navigation."""
        if models:
            return await self._select_model_interactive(models, provider)

        # No models discovered — fall back to free models or manual entry
        defaults = self.wizard.select_provider_defaults(provider)
        free_models = defaults.get("free_models", [])
        if free_models:
            self.console.print(f"Model ID for {provider}:", style="bold")
            self.console.print("  Free models:", style="muted")
            for m in free_models[:5]:
                self.console.print(f"    - {m}", style="muted")
            self.console.print()
            return await self._select_model_interactive(free_models, provider)

        # No models at all — manual entry
        self.console.print(f"Model ID for {provider}:", style="bold")
        self.console.print("  No models discovered — enter one manually", style="muted")
        self.console.print()
        session = PromptSession()
        model = (await session.prompt_async("Model ID > ")).strip()
        return model if model else None

    async def _select_model_interactive(self, models: list[str], provider: str) -> str | None:
        """Interactive model selection with arrow-key (↑/↓) navigation and scrolling."""
        from prompt_toolkit import Application
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.controls import FormattedTextControl
        from prompt_toolkit.layout.containers import HSplit, Window

        state = {"selected": 0, "filter": "", "scroll": 0}

        def get_filtered() -> list[str]:
            f = state["filter"].lower()
            if not f:
                return models
            return [m for m in models if f in m.lower()]

        def visible_count() -> int:
            try:
                rows = app.output.get_size().rows
            except Exception:
                rows = 25
            # title(1) + subtitle(1) + blank(1) + scroll hint(1) + hints(2) = 6
            return max(5, rows - 6)

        def get_text():
            filtered = get_filtered()
            if not filtered:
                return [("class:msg", "No matching models. Press Esc to enter manually.\n")]

            vcount = visible_count()
            scroll = state["scroll"]
            selected = state["selected"]

            # Keep the selection visible inside the scroll window.
            if selected < scroll:
                state["scroll"] = selected
                scroll = selected
            elif selected >= scroll + vcount:
                state["scroll"] = selected - vcount + 1
                scroll = state["scroll"]

            scroll = max(0, min(scroll, max(0, len(filtered) - vcount)))
            state["scroll"] = scroll

            result = []
            for i in range(scroll, min(scroll + vcount, len(filtered))):
                m = filtered[i]
                if i == selected:
                    result.append(("bold #E8FF3A reverse", f"  ▸ {i+1}. {m}\n"))
                else:
                    result.append(("", f"    {i+1}. {m}\n"))

            if len(filtered) > vcount:
                lo = scroll + 1
                hi = min(scroll + vcount, len(filtered))
                result.append(("class:msg", f"    … showing {lo}-{hi} of {len(filtered)}\n"))

            result.append(
                ("class:msg", "\n↑/↓ navigate  •  PgUp/PgDn scroll  •  Enter select  •  1-9 quick-pick  •  Type to filter  •  Esc cancel\n")
            )
            return result

        kb = KeyBindings()

        @kb.add("up")
        def _(event):
            filtered = get_filtered()
            if not filtered:
                return
            state["selected"] = max(0, state["selected"] - 1)

        @kb.add("down")
        def _(event):
            filtered = get_filtered()
            if not filtered:
                return
            state["selected"] = min(len(filtered) - 1, state["selected"] + 1)

        @kb.add("pageup")
        def _(event):
            filtered = get_filtered()
            if not filtered:
                return
            vcount = visible_count()
            state["selected"] = max(0, state["selected"] - vcount)
            state["scroll"] = max(0, state["scroll"] - vcount)

        @kb.add("pagedown")
        def _(event):
            filtered = get_filtered()
            if not filtered:
                return
            vcount = visible_count()
            state["selected"] = min(len(filtered) - 1, state["selected"] + vcount)
            state["scroll"] = min(max(0, len(filtered) - vcount), state["scroll"] + vcount)

        @kb.add("home")
        def _(event):
            filtered = get_filtered()
            if not filtered:
                return
            state["selected"] = 0
            state["scroll"] = 0

        @kb.add("end")
        def _(event):
            filtered = get_filtered()
            if not filtered:
                return
            state["selected"] = len(filtered) - 1
            state["scroll"] = max(0, len(filtered) - visible_count())

        @kb.add("enter")
        def _(event):
            filtered = get_filtered()
            if filtered:
                event.app.exit(result=filtered[state["selected"]])
            else:
                event.app.exit(result=None)

        @kb.add("c-c")
        @kb.add("escape")
        def _(event):
            event.app.exit(result=None)

        @kb.add("backspace")
        def _(event):
            state["filter"] = state["filter"][:-1]
            state["selected"] = 0
            state["scroll"] = 0

        for d in "123456789":
            @kb.add(d)
            def _(event, digit=d):
                filtered = get_filtered()
                idx = int(digit) - 1
                if idx < len(filtered):
                    event.app.exit(result=filtered[idx])

        layout = Layout(
            HSplit([
                Window(height=1, content=FormattedTextControl(lambda: [("bold", f"Model ID for {provider}:\n")])),
                Window(height=1, content=FormattedTextControl(lambda: [("class:msg", "Available models — ↑/↓ navigate, Enter select, type to filter:\n")])),
                Window(content=FormattedTextControl(get_text)),
            ])
        )

        app = Application(layout=layout, key_bindings=kb, full_screen=True)

        try:
            result = await app.run_async()
        except Exception:
            result = None

        if result is None:
            return None
        return str(result)

    def _show_complete(self, provider: str, model: str) -> None:
        """Render the completion panel."""
        self.console.print()
        self.console.print(
            Panel(
                Group(
                    Text("Setup complete!", style=f"bold {KRYTH_GOLD}", justify="center"),
                    Text(
                        f"Provider: {provider}  |  Model: {model}",
                        style="muted",
                        justify="center",
                    ),
                ),
                box=box.ROUNDED,
                border_style=KRYTH_GOLD,
                padding=(1, 2),
            )
        )
        self.console.print()
