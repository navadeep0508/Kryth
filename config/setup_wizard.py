"""First-launch setup wizard logic for KRYTH.

Detects missing configuration, walks the user through provider/model/API-key
setup, tests the connection, and saves credentials securely.
"""

from __future__ import annotations

import asyncio
import httpx
from dataclasses import dataclass, field
from typing import Any

from config.config import Config, ProviderConfig
from config.keyring_store import (
    save_credential,
    load_credential,
    list_stored_providers,
    migrate_legacy_credentials,
)
from config.providers import PROVIDER_DEFAULTS, ProviderType


@dataclass
class WizardResult:
    """Outcome of the setup wizard."""

    success: bool = False
    provider: str = ""
    model: str = ""
    base_url: str = ""
    message: str = ""


class SetupWizard:
    """Orchestrates the first-launch onboarding flow."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def is_first_launch(self) -> bool:
        """Return True when no provider credentials are configured anywhere."""
        if self.config.api_key:
            return False
        if list_stored_providers():
            return False
        # Legacy postal credentials count as configured.
        from config.credentials import load_credentials

        if load_credentials().get("api_key"):
            return False
        return True

    def select_provider_defaults(self, provider_key: str) -> dict[str, Any]:
        """Return default settings for a known provider key."""
        return PROVIDER_DEFAULTS.get(provider_key, {})

    def list_known_providers(self) -> list[str]:
        """Return known provider keys in a friendly order."""
        return [
            "openrouter",
            "nvidia",
            "groq",
            "openai",
            "anthropic",
            "google",
            "deepseek",
            "mistral",
            "together",
            "fireworks",
            "cerebras",
            "ollama",
            "lmstudio",
            "custom",
        ]

    async def test_connection(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
    ) -> tuple[bool, str, float]:
        """Test a provider connection with a minimal completion request.

        Returns (ok, message, latency_seconds).
        """
        import time

        start = time.monotonic()

        try:
            headers = {}
            if provider == "openrouter":
                headers["HTTP-Referer"] = "https://kryth.local"
                headers["X-Title"] = "KRYTH"

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        **headers,
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 5,
                        "stream": False,
                    },
                )

            latency = time.monotonic() - start

            if resp.status_code == 200:
                data = resp.json()
                if data.get("choices"):
                    return True, f"Connection successful ({latency:.2f}s)", latency
                return (
                    False,
                    f"Connected but unexpected response format ({latency:.2f}s)",
                    latency,
                )
            elif resp.status_code == 401:
                return False, "Authentication failed: invalid API key", latency
            elif resp.status_code == 404:
                return (
                    False,
                    f"Model '{model}' not found or not accessible",
                    latency,
                )
            else:
                return (
                    False,
                    f"HTTP {resp.status_code}: {resp.text[:200]}",
                    latency,
                )

        except httpx.ConnectError:
            return False, "Cannot connect: check your base URL and network", time.monotonic() - start
        except httpx.TimeoutException:
            return False, "Request timed out", time.monotonic() - start
        except Exception as e:  # pragma: no cover - network errors are varied
            return False, f"Connection error: {e}", time.monotonic() - start

    async def fetch_models(self, provider: str, base_url: str, api_key: str) -> list[str]:
        """Fetch available models from a provider's /models endpoint.

        Returns a list of model IDs, or an empty list if discovery fails.
        """
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://kryth.local"
            headers["X-Title"] = "KRYTH"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    models: list[str] = []
                    for item in data.get("data", []):
                        model_id = item.get("id")
                        if model_id:
                            models.append(model_id)
                    return models
        except Exception:
            pass

        return []

    def save_setup(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        organization: str | None = None,
        extra_headers: dict[str, str] | None = None,
        set_default: bool = True,
    ) -> None:
        """Save the configured provider and credentials."""
        provider_type = ProviderType.OPENAI
        for key, defaults in PROVIDER_DEFAULTS.items():
            if key == provider:
                provider_type = defaults["type"]
                break

        provider_config = ProviderConfig(
            name=provider,
            type=provider_type,
            base_url=base_url,
            api_key=api_key,
            model=model,
            organization=organization,
            extra_headers=extra_headers or {},
            is_default=set_default,
        )

        self.config.apply_provider(provider_config)

        # Save credentials securely (keyring preferred, TOML fallback)
        save_credential(provider, "api_key", api_key)
        if base_url:
            save_credential(provider, "base_url", base_url)
        save_credential(provider, "model", model)
        # Persist which profile setup selected. Config.apply_provider() only
        # affects this process; the loader restores this selection next run.
        save_credential("_kryth", "active_provider", provider)

        # Migrate any legacy postal credentials
        migrate_legacy_credentials()


def _select_provider(wizard: SetupWizard, console: Console) -> str | None:
    """Select a provider using arrow key navigation."""
    from prompt_toolkit.input import create_input
    from prompt_toolkit.keys import Keys

    known = wizard.list_known_providers()
    selected = 0

    def render() -> None:
        console.clear()
        console.print("[1/5] Select your AI provider:", style="bold")
        console.print("↑↓ navigate  ·  Enter select  ·  Esc cancel", style="dim")
        console.print()
        for i, p in enumerate(known):
            defaults = wizard.select_provider_defaults(p)
            ptype = defaults.get("type", ProviderType.OPENAI)
            if i == selected:
                console.print(f"  ▸ {p} ({ptype.value})", style="bold #E8FF3A")
            else:
                console.print(f"    {p} ({ptype.value})", style="muted")

    render()

    try:
        device = create_input()
        with device.raw_mode():
            for key_press in device.read_keys():
                if key_press.key == Keys.Up:
                    selected = max(0, selected - 1)
                    render()
                elif key_press.key == Keys.Down:
                    selected = min(len(known) - 1, selected + 1)
                    render()
                elif key_press.key in (Keys.ControlM, Keys.ControlJ):
                    return known[selected]
                elif key_press.key in (Keys.ControlC, Keys.Escape):
                    return None
    except Exception:
        return None


def run_wizard_interactive(wizard: SetupWizard) -> WizardResult:
    """Run the setup wizard interactively using prompt_toolkit.

    This is a synchronous wrapper that collects input step by step.
    """
    from prompt_toolkit import PromptSession
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich import box

    console = Console()
    session = PromptSession(completer=None)

    console.print()
    console.print(
        Panel(
            Text("KRYTH Setup Wizard", style="bold #E8FF3A", justify="center"),
            Text(
                "Configure your AI provider to get started",
                style="muted",
                justify="center",
            ),
            box=box.ROUNDED,
            border_style="#E8FF3A",
            padding=(1, 2),
        )
    )
    console.print()

    # Step 1: Provider selection (arrow key navigation)
    provider_key = _select_provider(wizard, console)
    if provider_key is None:
        return WizardResult(success=False, message="No provider selected")

    defaults = wizard.select_provider_defaults(provider_key)
    default_base_url = defaults.get("base_url", "")
    default_type = defaults.get("type", ProviderType.OPENAI)

    # Step 2: Base URL
    console.print()
    console.print(f"[2/5] Base URL for {provider_key}:", style="bold")
    if default_base_url:
        console.print(f"  Default: {default_base_url}", style="muted")
    base_url = session.prompt("Base URL > ", default=default_base_url).strip()
    if not base_url:
        base_url = default_base_url

    # Step 3: Model
    console.print()
    console.print(f"[3/5] Model ID for {provider_key}:", style="bold")
    free_models = defaults.get("free_models", [])
    if free_models:
        console.print("  Free models:", style="muted")
        for m in free_models[:5]:
            console.print(f"    - {m}", style="muted")
    default_model = free_models[0] if free_models else ""
    model = session.prompt("Model ID > ", default=default_model).strip()
    if not model:
        return WizardResult(success=False, message="No model specified")

    # Step 4: API key (masked)
    console.print()
    console.print(f"[4/5] API key for {provider_key}:", style="bold")
    console.print("  Input will be hidden as you type", style="muted")
    api_key = session.prompt("API key > ", hide_input=True).strip()
    if not api_key:
        return WizardResult(success=False, message="No API key entered")

    # Optional organization
    console.print()
    org = session.prompt("Organization ID (optional) > ").strip()
    organization = org if org else None

    # Step 5: Test connection
    console.print()
    console.print("[5/5] Testing connection...", style="bold")

    ok, message, latency = asyncio.run(
        wizard.test_connection(provider_key, base_url, api_key, model)
    )

    if ok:
        console.print(f"  [ok] {message}", style="bold green")
    else:
        console.print(f"  [fail] {message}", style="bold red")
        retry = session.prompt("Retry with different settings? (y/N) > ").strip().lower()
        if retry != "y":
            return WizardResult(success=False, message="Connection test failed")

        # Re-run test with same values (user can re-run wizard)
        ok, message, latency = asyncio.run(
            wizard.test_connection(provider_key, base_url, api_key, model)
        )
        if not ok:
            return WizardResult(success=False, message=f"Connection test failed: {message}")

    # Save
    wizard.save_setup(
        provider=provider_key,
        base_url=base_url,
        api_key=api_key,
        model=model,
        organization=organization,
    )

    console.print()
    console.print(
        Panel(
            Text("Setup complete!", style="bold #E8FF3A", justify="center"),
            Text(
                f"Provider: {provider_key}  |  Model: {model}",
                style="muted",
                justify="center",
            ),
            box=box.ROUNDED,
            border_style="#E8FF3A",
            padding=(1, 2),
        )
    )
    console.print()

    return WizardResult(
        success=True,
        provider=provider_key,
        model=model,
        base_url=base_url,
        message="Setup complete",
    )
