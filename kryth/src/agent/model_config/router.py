"""Role-based LLM routing — maps task roles to (client, model_name) pairs.

Usage:
    from agent.model_config import get_llm

    client, model = get_llm("planner")
    client, model = get_llm("vision")
    client, model = get_llm()        # defaults to "main"
"""

from __future__ import annotations

import os
from typing import Any

from agent.env import getenv
from agent.model_config.schema import (
    ROLES, DEFAULT_VISION_MODEL, DEFAULT_NVIDIA_BASE_URL
)


# ---------------------------------------------------------------------------
# Client cache (one client per unique provider+key+url combination)
# ---------------------------------------------------------------------------

_client_cache: dict[str, Any] = {}


def _cache_key(provider: str, api_key: str, base_url: str) -> str:
    return f"{provider}|{api_key[:8]}|{base_url}"


def _get_or_create_client(provider: str, api_key: str, base_url: str, **kwargs) -> Any:
    key = _cache_key(provider, api_key, base_url)
    if key not in _client_cache:
        from agent.model_config.providers import make_client
        _client_cache[key] = make_client(
            provider_name=provider,
            api_key=api_key,
            base_url=base_url,
            **kwargs,
        )
    return _client_cache[key]


def invalidate_cache() -> None:
    """Clear the client cache (call after /config changes)."""
    _client_cache.clear()


# ---------------------------------------------------------------------------
# Core routing
# ---------------------------------------------------------------------------

def get_llm(role: str = "main") -> tuple[Any, str]:
    """Return (client, model_name) for the given role.

    Falls back gracefully:
      specialized mode: use role's ModelSpec
      unified mode: use single provider/model for all roles
      env-only mode (no YAML): reconstruct from env vars (backward compat)
    """
    from agent.model_config.loader import get_config

    cfg = get_config()

    # ---- Specialized mode ----
    if cfg.mode == "specialized" and cfg.models:
        spec = cfg.models.get(role) or cfg.models.get("main")
        if spec and spec.model:
            # Find provider config
            pcfg = cfg.providers.get(spec.provider)
            api_key = (pcfg.api_key if pcfg else "") or _env_key_for(spec.provider)
            base_url = (pcfg.base_url if pcfg else "") or getenv("KRYTH_BASE_URL") or _default_url(spec.provider)
            timeout = pcfg.timeout if pcfg else 60.0
            client = _get_or_create_client(
                provider=spec.provider,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            return client, spec.model

    # ---- Unified mode or fallback ----
    api_key = cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    base_url = cfg.base_url or getenv("KRYTH_BASE_URL", "https://api.openai.com/v1")
    provider = cfg.provider or _infer_provider(base_url)

    # Vision role: prefer NVIDIA if key available and no model configured
    if role == "vision":
        nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        vision_model = getenv("KRYTH_VISION_MODEL", DEFAULT_VISION_MODEL)
        if nvidia_key:
            client = _get_or_create_client(
                provider="nvidia",
                api_key=nvidia_key,
                base_url=DEFAULT_NVIDIA_BASE_URL,
            )
            return client, vision_model

    # Role-specific env var overrides (backward compat)
    model = _env_model_for(role) or cfg.model or _default_model(role)

    client = _get_or_create_client(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
    )
    return client, model


def get_model_name(role: str = "main") -> str:
    """Return just the model name for a given role."""
    _, model = get_llm(role)
    return model


def get_client(role: str = "main") -> Any:
    """Return just the client for a given role."""
    client, _ = get_llm(role)
    return client


# ---------------------------------------------------------------------------
# Task-type routing
# ---------------------------------------------------------------------------

def pick_model_for_task(task_type: str = "", **hints) -> tuple[Any, str]:
    """Map a task type to the appropriate role and return (client, model).

    Uses the routing table from config, falls back to 'main'.
    """
    from agent.model_config.loader import get_config
    cfg = get_config()

    _BUILTIN_ROUTING = {
        "browser_tasks":      "main",
        "image_tasks":        "vision",
        "planning_tasks":     "planner",
        "extraction_tasks":   "extraction",
        "complex_reasoning":  "reasoning",
        "small_tasks":        "summary",
        "summarization":      "summary",
        "coding":             "main",
        "conversation":       "main",
    }

    role = (
        cfg.routing.get(task_type)
        or _BUILTIN_ROUTING.get(task_type)
        or "main"
    )
    return get_llm(role)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_model_for(role: str) -> str:
    """Read role-specific env var overrides."""
    mapping = {
        "main":       "KRYTH_MAIN_MODEL",
        "planner":    "KRYTH_PLANNER_MODEL",
        "summary":    "KRYTH_SUMMARIZER_MODEL",
        "vision":     "KRYTH_VISION_MODEL",
        "extraction": "KRYTH_EXTRACTION_MODEL",
        "reasoning":  "KRYTH_REASONING_MODEL",
    }
    env_var = mapping.get(role, "")
    return getenv(env_var) if env_var else ""


def _env_key_for(provider: str) -> str:
    from agent.model_config.providers import PROVIDER_KEY_ENV
    env_var = PROVIDER_KEY_ENV.get(provider, "")
    return os.environ.get(env_var, "").strip() if env_var else ""


def _default_url(provider: str) -> str:
    from agent.model_config.providers import PROVIDER_DEFAULTS
    return PROVIDER_DEFAULTS.get(provider, {}).get("base_url", "https://api.openai.com/v1")


def _infer_provider(base_url: str) -> str:
    """Guess provider name from the base URL."""
    url = base_url.lower()
    if "openrouter" in url:
        return "openrouter"
    if "anthropic" in url:
        return "anthropic"
    if "generativelanguage" in url or "google" in url:
        return "google"
    if "nvidia" in url or "nvapi" in url:
        return "nvidia"
    if "localhost" in url or "127.0.0.1" in url:
        # Assume Ollama or LM Studio
        return "ollama"
    return "openai"


def _default_model(role: str) -> str:
    """Return a sensible default when no model is configured for a role."""
    defaults = {
        "main":       getenv("KRYTH_MAIN_MODEL", "gpt-4o-mini"),
        "planner":    getenv("KRYTH_PLANNER_MODEL", "gpt-4o-mini"),
        "summary":    getenv("KRYTH_SUMMARIZER_MODEL", "gpt-4o-mini"),
        "vision":     getenv("KRYTH_VISION_MODEL", DEFAULT_VISION_MODEL),
        "extraction": getenv("KRYTH_EXTRACTION_MODEL",
                             getenv("KRYTH_MAIN_MODEL", "gpt-4o-mini")),
        "reasoning":  getenv("KRYTH_REASONING_MODEL",
                             getenv("KRYTH_MAIN_MODEL", "gpt-4o-mini")),
    }
    return defaults.get(role, getenv("KRYTH_MAIN_MODEL", "gpt-4o-mini"))
