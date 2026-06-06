"""Fallback + retry logic for LLM calls.

Wraps sync and async calls with:
  1. Retry with exponential backoff on transient errors
  2. Model-level fallbacks (try next model on failure)
  3. Role fallback (fall back to unified model if specialized fails)
"""

from __future__ import annotations

import time
from typing import Any, Callable


RETRY_DELAYS = (0.5, 1.5, 4.0)


def _interruptible_sleep(seconds: float) -> None:
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def _retryable(exc: Exception) -> bool:
    """Return True for transient errors worth retrying."""
    try:
        from openai import (
            APIConnectionError, APITimeoutError,
            RateLimitError, InternalServerError,
        )
        return isinstance(exc, (APIConnectionError, APITimeoutError,
                                RateLimitError, InternalServerError))
    except ImportError:
        return False


def with_retry(fn: Callable, *args, label: str = "llm", **kwargs) -> Any:
    """Synchronous call with exponential backoff retry."""
    from agent import ui
    last_exc: Exception | None = None
    total = len(RETRY_DELAYS) + 1

    for attempt in range(1, total + 1):
        try:
            return fn(*args, **kwargs)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if not _retryable(e):
                raise
            last_exc = e
            if attempt >= total:
                break
            delay = RETRY_DELAYS[attempt - 1]
            try:
                ui.llm_retry(
                    label=label, attempt=attempt,
                    total=total, reason=type(e).__name__, delay=delay,
                )
            except Exception:
                pass
            _interruptible_sleep(delay)

    raise last_exc  # type: ignore[misc]


def with_model_fallback(
    role: str,
    fn: Callable[[Any, str], Any],
    label: str = "llm",
) -> Any:
    """Try primary model then fallbacks for a given role.

    fn(client, model_name) → result

    Fallback chain:
      1. Role's primary model
      2. Role's configured fallbacks (from config)
      3. Unified model (if in unified mode)
    """
    from agent.model_config.loader import get_config
    from agent.model_config.router import get_llm, _get_or_create_client, _env_key_for, _default_url

    cfg = get_config()
    spec = cfg.models.get(role) if cfg.mode == "specialized" else None

    # Build candidate (provider, model) list
    candidates: list[tuple[str, str]] = []

    if spec and spec.model:
        candidates.append((spec.provider, spec.model))
        # Add model-level fallbacks
        provider = spec.provider
        for fb_model in (spec.fallbacks or []):
            candidates.append((provider, fb_model))
        # Add role-level fallbacks from config
        for fb_model in (cfg.fallbacks.get(role) or []):
            candidates.append((provider, fb_model))

    # Always add the unified/main client as final fallback
    primary_client, primary_model = get_llm("main")
    if not candidates or (len(candidates) == 1 and
                          candidates[0][1] == primary_model):
        # Only one option — just use it with retry
        return with_retry(fn, primary_client, primary_model, label=label)

    # Try each candidate
    last_exc: Exception | None = None
    for provider, model in candidates:
        try:
            if provider and cfg.mode == "specialized":
                pcfg = cfg.providers.get(provider)
                api_key = (pcfg.api_key if pcfg else "") or _env_key_for(provider)
                base_url = (pcfg.base_url if pcfg else "") or _default_url(provider)
                client = _get_or_create_client(provider, api_key, base_url)
            else:
                client = primary_client

            return with_retry(fn, client, model, label=f"{label}:{model}")
        except KeyboardInterrupt:
            raise
        except Exception as e:
            last_exc = e
            continue

    # All candidates failed — use main as last resort
    try:
        return with_retry(fn, primary_client, primary_model, label=f"{label}:main-fallback")
    except Exception as e:
        raise last_exc or e
