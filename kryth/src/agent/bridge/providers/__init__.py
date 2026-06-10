"""Provider adapters for the local browser bridge.

Each provider module exposes a class that inherits from BaseProvider:
  - gemini.py    → GeminiProvider
  - claude.py    → ClaudeProvider
  - openai_browser.py → OpenAIBrowserProvider

All providers share the same interface so the bridge server can swap
them transparently based on the KRYTH_BRIDGE_PROVIDER env var.
"""

from __future__ import annotations

from agent.bridge.providers.base import BaseProvider, ProviderError


def get_provider(name: str) -> "type[BaseProvider]":
    """Return the provider class for the given name."""
    name = name.lower().strip()
    if name == "gemini":
        from agent.bridge.providers.gemini import GeminiProvider
        return GeminiProvider
    if name in ("claude", "anthropic"):
        from agent.bridge.providers.claude import ClaudeProvider
        return ClaudeProvider
    if name in ("openai", "chatgpt"):
        from agent.bridge.providers.openai_browser import OpenAIBrowserProvider
        return OpenAIBrowserProvider
    raise ValueError(
        f"Unknown provider '{name}'. "
        "Valid options: gemini, claude, openai"
    )


__all__ = ["BaseProvider", "ProviderError", "get_provider"]
