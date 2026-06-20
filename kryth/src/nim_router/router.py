"""Core routing engine: primary → fallback1 → fallback2 with TTFT timeout.

Fallback is triggered on:
  - asyncio.TimeoutError  (TTFT deadline exceeded)
  - APITimeoutError       (httpx read/connect timeout)
  - APIConnectionError    (network failure)
  - RateLimitError        (429)
  - InternalServerError   (5xx)
  - APIStatusError 404/422/502/503/504

Auth errors (401/403) and bad-request errors (400) are re-raised immediately —
they indicate a configuration problem, not a transient model failure.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import httpx
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from config import BASE_URL, MODEL_CHAINS, ModelEntry, get_api_key
from metrics import RequestMetrics, Timer, setup_logging
from models import ModelRole, RouteResult


# ── Error classification ──────────────────────────────────────────────────────

_ALWAYS_FALLBACK = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
    asyncio.TimeoutError,
)

_FALLBACK_STATUS_CODES = {404, 422, 429, 500, 502, 503, 504}


def _should_fallback(exc: Exception) -> bool:
    if isinstance(exc, _ALWAYS_FALLBACK):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in _FALLBACK_STATUS_CODES
    return False


# ── Usage extraction ──────────────────────────────────────────────────────────

def _extract_usage(obj: Any) -> dict[str, int]:
    if obj is None:
        return {}
    return {
        "prompt_tokens":     getattr(obj, "prompt_tokens",     0) or 0,
        "completion_tokens": getattr(obj, "completion_tokens", 0) or 0,
        "total_tokens":      getattr(obj, "total_tokens",      0) or 0,
    }


# ── Router ────────────────────────────────────────────────────────────────────

class NIMRouter:
    """Multi-model router for NVIDIA NIM with automatic 3-tier fallback.

    Example::

        router = NIMRouter()
        result = await router.route(
            "main",
            messages=[{"role": "user", "content": "Hello!"}],
            on_chunk=lambda piece: print(piece, end="", flush=True),
        )
        print(result.model_used, result.ttft_ms)
    """

    def __init__(self, log_level: str = "INFO") -> None:
        self._log: logging.Logger = setup_logging(log_level)
        self._client = AsyncOpenAI(
            base_url=BASE_URL,
            api_key=get_api_key(),
            # Outer httpx timeouts — generous because TTFT enforcement is done
            # per-model via asyncio.timeout() below.
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0),
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def route(
        self,
        role: str | ModelRole,
        messages: list[dict],
        on_chunk: Callable[[str], None] | None = None,
        **create_kwargs,
    ) -> RouteResult:
        """Route a request through the model chain with automatic fallback.

        Args:
            role:          "main" | "planner" | "summarizer" | "vision"
                           (or the corresponding ModelRole enum value).
            messages:      OpenAI-format message list.
            on_chunk:      Optional sync callback invoked with each streamed token.
            **create_kwargs: Forwarded to chat.completions.create()
                             (e.g. temperature=0.7, max_tokens=2048).

        Returns:
            RouteResult with full response and all captured metrics.

        Raises:
            RuntimeError:  All models in the chain failed.
            ValueError:    Unknown role name.
        """
        role_str = role.value if isinstance(role, ModelRole) else role
        chain = MODEL_CHAINS.get(role_str)
        if chain is None:
            raise ValueError(
                f"Unknown role '{role_str}'. Valid roles: {sorted(MODEL_CHAINS)}"
            )

        metrics = RequestMetrics(role=role_str)
        timer = Timer()

        for level, entry in enumerate(chain.models):
            metrics.models_attempted.append(entry.name)
            self._log.debug({
                "event": "trying_model",
                "role": role_str,
                "model": entry.name,
                "level": level,
            })

            try:
                content, usage = await self._stream_model(
                    entry=entry,
                    messages=messages,
                    timer=timer,
                    on_chunk=on_chunk,
                    **create_kwargs,
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                metrics.errors.append({
                    "model":  entry.name,
                    "level":  level,
                    "error":  type(exc).__name__,
                    "detail": str(exc)[:300],
                })
                self._log.warning({
                    "event":  "model_failed",
                    "role":   role_str,
                    "model":  entry.name,
                    "level":  level,
                    "error":  type(exc).__name__,
                    "detail": str(exc)[:200],
                })
                if _should_fallback(exc) and level < len(chain) - 1:
                    continue   # try next model
                # Non-recoverable or last model — fall through to failure
                break

            else:
                # ── Success ───────────────────────────────────────────────────
                metrics.model_selected    = entry.name
                metrics.fallback_level    = level
                metrics.ttft_ms           = timer.ttft_ms
                metrics.total_latency_ms  = timer.elapsed_ms
                metrics.prompt_tokens     = usage.get("prompt_tokens", 0)
                metrics.completion_tokens = usage.get("completion_tokens", 0)
                metrics.total_tokens      = usage.get("total_tokens", 0)
                metrics.success           = True
                metrics.log(self._log)

                return RouteResult(
                    role=role_str,
                    model_used=entry.name,
                    fallback_level=level,
                    content=content,
                    ttft_ms=metrics.ttft_ms,
                    total_latency_ms=metrics.total_latency_ms,
                    prompt_tokens=metrics.prompt_tokens,
                    completion_tokens=metrics.completion_tokens,
                    total_tokens=metrics.total_tokens,
                    errors=list(metrics.errors),
                )

        # ── All models exhausted ──────────────────────────────────────────────
        metrics.total_latency_ms = timer.elapsed_ms
        metrics.log(self._log)
        raise RuntimeError(
            f"All models failed for role '{role_str}'. "
            f"Attempted: {metrics.models_attempted}. "
            f"Errors: {[e['error'] for e in metrics.errors]}"
        )

    # ── Internal streaming ────────────────────────────────────────────────────

    async def _stream_model(
        self,
        entry: ModelEntry,
        messages: list[dict],
        timer: Timer,
        on_chunk: Callable[[str], None] | None,
        **create_kwargs,
    ) -> tuple[str, dict]:
        """Stream a single model. Returns (full_content, usage_dict).

        Phase 1 — waits up to ``entry.timeout_s`` for the first content token.
                   Raises asyncio.TimeoutError if the deadline passes.
        Phase 2 — drains the remaining stream with no additional deadline.
        """
        content_parts: list[str] = []
        usage: dict[str, int] = {}

        stream = await self._client.chat.completions.create(
            model=entry.name,
            messages=messages,
            stream=True,
            **create_kwargs,
        )

        async with stream:
            aiter = stream.__aiter__()

            # ── Phase 1: first-token with TTFT timeout ────────────────────────
            try:
                async with asyncio.timeout(entry.timeout_s):
                    while True:
                        chunk = await aiter.__anext__()
                        piece = _chunk_content(chunk)
                        if piece:
                            timer.mark_first_token()
                            content_parts.append(piece)
                            if on_chunk:
                                on_chunk(piece)
                            break
                        _collect_usage(chunk, usage)

            except StopAsyncIteration:
                # Stream ended before any content arrived
                if not content_parts:
                    raise RuntimeError(
                        f"Empty response (no content) from {entry.name}"
                    )
                return "".join(content_parts), usage

            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"TTFT timeout ({entry.timeout_s}s) exceeded for {entry.name}"
                )

            # ── Phase 2: drain remainder (no deadline) ─────────────────────────
            try:
                while True:
                    chunk = await aiter.__anext__()
                    piece = _chunk_content(chunk)
                    if piece:
                        content_parts.append(piece)
                        if on_chunk:
                            on_chunk(piece)
                    _collect_usage(chunk, usage)
            except StopAsyncIteration:
                pass

        return "".join(content_parts), usage


# ── Chunk helpers ─────────────────────────────────────────────────────────────

def _chunk_content(chunk: Any) -> str:
    """Extract text content from a streaming chunk, or '' if none."""
    try:
        delta = chunk.choices[0].delta if chunk.choices else None
        return (delta.content or "") if delta else ""
    except (IndexError, AttributeError):
        return ""


def _collect_usage(chunk: Any, usage: dict) -> None:
    """Merge usage data from a streaming chunk in-place."""
    u = getattr(chunk, "usage", None)
    if u:
        usage.update(_extract_usage(u))
