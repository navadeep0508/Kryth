"""Provider registry for KRYTH - supports multiple AI provider profiles.

Multi-provider support for OpenAI, Anthropic, Google, OpenRouter, Groq,
NVIDIA NIM, DeepSeek, Mistral, Together AI, Fireworks AI, Cerebras, Ollama,
LM Studio, and custom OpenAI-compatible endpoints.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator


class ProviderType(str, Enum):
    """Provider categories determine which client and auth scheme to use."""

    OPENAI = "openai"           # OpenAI-compatible (OpenRouter, Groq, NVIDIA NIM, etc.)
    ANTHROPIC = "anthropic"     # Anthropic native API
    GOOGLE = "google"           # Google Gemini native API
    OLLAMA = "ollama"           # Ollama local API
    CUSTOM = "custom"           # User-defined OpenAI-compatible endpoint


# Default base URLs for well-known providers
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "openrouter": {
        "type": ProviderType.OPENAI,
        "base_url": "https://openrouter.ai/api/v1",
        "free_models": [
            "nvidia/nemotron-nano-7b-v2:free",
            "nvidia/llama-3.1-nemotron-70b-instruct:free",
            "meta-llama/llama-3.2-3b-instruct:free",
            "google/gemma-2-2b-it:free",
            "microsoft/phi-3-mini-128k-instruct:free",
        ],
    },
    "openai": {
        "type": ProviderType.OPENAI,
        "base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "type": ProviderType.ANTHROPIC,
        "base_url": "https://api.anthropic.com/v1",
    },
    "google": {
        "type": ProviderType.GOOGLE,
        "base_url": "https://generativelanguage.googleapis.com/v1",
    },
    "groq": {
        "type": ProviderType.OPENAI,
        "base_url": "https://api.groq.com/openai/v1",
    },
    "nvidia": {
        "type": ProviderType.OPENAI,
        "base_url": "https://integrate.api.nvidia.com/v1",
    },
    "deepseek": {
        "type": ProviderType.OPENAI,
        "base_url": "https://api.deepseek.com/v1",
    },
    "mistral": {
        "type": ProviderType.OPENAI,
        "base_url": "https://api.mistral.ai/v1",
    },
    "together": {
        "type": ProviderType.OPENAI,
        "base_url": "https://api.together.xyz/v1",
    },
    "fireworks": {
        "type": ProviderType.OPENAI,
        "base_url": "https://api.fireworks.ai/inference/v1",
    },
    "cerebras": {
        "type": ProviderType.OPENAI,
        "base_url": "https://api.cerebras.ai/v1",
    },
    "ollama": {
        "type": ProviderType.OLLAMA,
        "base_url": "http://localhost:11434/v1",
    },
    "lmstudio": {
        "type": ProviderType.OPENAI,
        "base_url": "http://localhost:1234/v1",
    },
}


class ProviderConfig(BaseModel):
    """A single provider profile with credentials and model settings."""

    name: str
    type: ProviderType = ProviderType.OPENAI
    base_url: str
    api_key: str = ""
    model: str = ""
    organization: str | None = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, v: str) -> str:
        return v.strip().lower().replace(" ", "-")


class ProviderRegistry(BaseModel):
    """Manages multiple provider profiles and selects the active one."""

    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    active_name: str = ""

    model_config = {"arbitrary_types_allowed": True}

    def add(self, provider: ProviderConfig) -> None:
        self.providers[provider.name] = provider
        if provider.is_default or not self.active_name:
            self.active_name = provider.name
            self._set_default(provider)

    def _set_default(self, provider: ProviderConfig) -> None:
        for p in self.providers.values():
            p.is_default = p.name == provider.name

    def get(self, name: str | None = None) -> ProviderConfig | None:
        name = name or self.active_name
        if not name:
            return None
        return self.providers.get(name)

    def set_active(self, name: str) -> bool:
        if name not in self.providers:
            return False
        self._set_default(self.providers[name])
        self.active_name = name
        return True

    def list_names(self) -> list[str]:
        return list(self.providers.keys())

    def remove(self, name: str) -> bool:
        if name not in self.providers:
            return False
        del self.providers[name]
        if self.active_name == name:
            self.active_name = self.list_names()[0] if self.providers else ""
        return True

    def get_defaults(self, provider_key: str) -> dict[str, Any] | None:
        return PROVIDER_DEFAULTS.get(provider_key)

    def to_config_overrides(self, provider: ProviderConfig) -> dict[str, Any]:
        """Convert a provider profile into Config-compatible overrides."""
        overrides: dict[str, Any] = {
            "base_url": provider.base_url,
            "model_name": provider.model,
        }
        if provider.organization:
            overrides["organization"] = provider.organization
        if provider.extra_headers:
            overrides["extra_headers"] = provider.extra_headers
        return overrides