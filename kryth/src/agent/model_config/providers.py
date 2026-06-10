"""Provider registry — factory functions for each LLM provider.

Each provider returns an OpenAI-compatible client (or native SDK wrapper).
Adding a new provider requires only one entry in PROVIDER_REGISTRY.

Provider types supported out of the box:
  openai         — OpenAI / Azure OpenAI
  openrouter     — OpenRouter (OpenAI-compat)
  anthropic      — Anthropic native SDK
  google         — Google Generative AI
  nvidia         — NVIDIA NIM (OpenAI-compat)
  ollama         — Ollama local (OpenAI-compat)
  lmstudio       — LM Studio (OpenAI-compat)
  openai_compat  — Any generic OpenAI-compatible endpoint
"""

from __future__ import annotations

import os
from typing import Any

import httpx


# ---------------------------------------------------------------------------
# OpenAI-compatible factory (used for most providers)
# ---------------------------------------------------------------------------

def _make_openai_compat(
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 60.0,
    max_retries: int = 3,
    headers: dict | None = None,
    **_kwargs: Any,
):
    """Create an OpenAI client pointed at any OpenAI-compat endpoint."""
    from openai import OpenAI

    key = (api_key or "not-configured").strip() or "not-configured"
    return OpenAI(
        api_key=key,
        base_url=base_url.rstrip("/") if base_url else "https://api.openai.com/v1",
        timeout=httpx.Timeout(
            connect=15.0,
            read=max(timeout, 30.0),
            write=60.0,
            pool=15.0,
        ),
        max_retries=max_retries,
        default_headers=headers or {},
    )


def _make_anthropic_compat(
    api_key: str,
    base_url: str = "https://api.anthropic.com",
    timeout: float = 60.0,
    max_retries: int = 3,
    headers: dict | None = None,
    **_kwargs: Any,
):
    """Create an Anthropic client (native SDK, if installed)."""
    try:
        import anthropic
        return anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=base_url or "https://api.anthropic.com",
            timeout=timeout,
            max_retries=max_retries,
            default_headers=headers or {},
        )
    except ImportError:
        # Fall back to OpenAI-compat if anthropic SDK not installed
        return _make_openai_compat(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY", ""),
            base_url=(base_url or "https://api.anthropic.com") + "/v1",
            timeout=timeout,
            max_retries=max_retries,
            headers={"anthropic-version": "2023-06-01", **(headers or {})},
        )


def _make_google_compat(
    api_key: str,
    base_url: str = "",
    timeout: float = 60.0,
    max_retries: int = 3,
    headers: dict | None = None,
    **_kwargs: Any,
):
    """Google Generative AI via OpenAI-compat endpoint."""
    key = api_key or os.environ.get("GOOGLE_API_KEY", "")
    url = base_url or f"https://generativelanguage.googleapis.com/v1beta/openai"
    return _make_openai_compat(
        api_key=key,
        base_url=url,
        timeout=timeout,
        max_retries=max_retries,
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY: dict[str, Any] = {
    "openai":        _make_openai_compat,
    "openrouter":    _make_openai_compat,
    "nvidia":        _make_openai_compat,
    "ollama":        _make_openai_compat,
    "lmstudio":      _make_openai_compat,
    "openai_compat": _make_openai_compat,
    "anthropic":     _make_anthropic_compat,
    "google":        _make_google_compat,
    "gemini":        _make_google_compat,
}

# Default base URLs per provider (used when config omits base_url)
PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "openai":     {"base_url": "https://api.openai.com/v1"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
    "nvidia":     {"base_url": "https://integrate.api.nvidia.com/v1"},
    "ollama":     {"base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
                   "api_key": "ollama"},
    "lmstudio":   {"base_url": os.environ.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
                   "api_key": "lm-studio"},
    "anthropic":  {"base_url": "https://api.anthropic.com"},
    "google":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
}

# Environment variable names for API keys per provider
PROVIDER_KEY_ENV: dict[str, str] = {
    "openai":     "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic":  "ANTHROPIC_API_KEY",
    "google":     "GOOGLE_API_KEY",
    "gemini":     "GOOGLE_API_KEY",
    "nvidia":     "NVIDIA_API_KEY",
    "ollama":     "",
    "lmstudio":   "",
}


def make_client(
    provider_name: str,
    api_key: str = "",
    base_url: str = "",
    timeout: float = 60.0,
    max_retries: int = 3,
    headers: dict | None = None,
    **kwargs: Any,
):
    """Instantiate an LLM client for the given provider name.

    Falls back to ``openai_compat`` for unknown provider names so new
    providers only need a registry entry, not a code change.
    """
    factory = PROVIDER_REGISTRY.get(provider_name, _make_openai_compat)
    defaults = PROVIDER_DEFAULTS.get(provider_name, {})

    resolved_key = (
        api_key
        or os.environ.get(PROVIDER_KEY_ENV.get(provider_name, ""), "")
        or defaults.get("api_key", "")
    ).strip()

    resolved_url = (
        base_url
        or defaults.get("base_url", "https://api.openai.com/v1")
    )

    return factory(
        api_key=resolved_key,
        base_url=resolved_url,
        timeout=timeout,
        max_retries=max_retries,
        headers=headers,
        **kwargs,
    )


def list_providers() -> list[str]:
    """Return all registered provider names."""
    return sorted(PROVIDER_REGISTRY.keys())
