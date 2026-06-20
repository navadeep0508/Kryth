"""Reliability layer for provider failure isolation and retry decisions.

Provides:
- Error classification into provider vs. non-provider failures
- Provider health tracking (success rates, error counts)
- Retry policy with exponential backoff
"""

from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class ErrorCategory(Enum):
    """Classification of errors for retry decisions."""
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    MALFORMED = "malformed"
    PROVIDER = "provider"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    API_ERROR = "api_error"
    INTERRUPTED = "interrupted"
    MAX_TURNS = "max_turns"
    UNKNOWN = "unknown"


@dataclass
class RetryDecision:
    """Result of a retry policy decision."""
    should_retry: bool
    backoff_s: float
    reason: str = ""


class RetryPolicy:
    """Determines whether and how to retry based on error category and attempt count."""

    def __init__(self):
        # Maximum retries per error category
        self._max_retries = {
            ErrorCategory.TIMEOUT: 3,
            ErrorCategory.RATE_LIMIT: 3,
            ErrorCategory.MALFORMED: 2,
            ErrorCategory.PROVIDER: 2,
            ErrorCategory.PAYLOAD_TOO_LARGE: 1,
            ErrorCategory.API_ERROR: 1,
            ErrorCategory.INTERRUPTED: 0,
            ErrorCategory.MAX_TURNS: 0,
            ErrorCategory.UNKNOWN: 0,
        }
        # Base backoff in seconds
        self._base_backoff = 1.0
        # Maximum backoff cap
        self._max_backoff = 8.0

    def decide(self, category: ErrorCategory, attempt: int) -> RetryDecision:
        """Decide whether to retry given the error category and current attempt number.

        Args:
            category: The classified error category
            attempt: Current retry attempt (1-based)

        Returns:
            RetryDecision with should_retry flag and backoff seconds
        """
        max_retries = self._max_retries.get(category, 0)
        if attempt > max_retries:
            return RetryDecision(False, 0.0, f"max retries ({max_retries}) exceeded")

        # Exponential backoff: base * 2^(attempt-1) with jitter
        backoff = min(self._base_backoff * (2 ** (attempt - 1)), self._max_backoff)
        jitter = backoff * 0.1 * (hash(str(attempt)) % 3 - 1)  # ±10% jitter
        backoff = max(0.1, backoff + jitter)

        return RetryDecision(True, backoff, f"retry allowed (attempt {attempt}/{max_retries})")


@dataclass
class ProviderMetrics:
    """Health metrics for a single provider."""
    successes: int = 0
    failures: int = 0
    provider_errors: int = 0  # classified as provider failures
    last_error: Optional[ErrorCategory] = None
    last_error_time: Optional[float] = None

    @property
    def total_requests(self) -> int:
        return self.successes + self.failures

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 1.0
        return self.successes / self.total_requests

    @property
    def provider_error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.provider_errors / self.total_requests


class ProviderHealth:
    """Global tracker for provider health across all agents.

    Records successes and failures by provider (identified by KRYTH_BASE_URL).
    Thread-safe for concurrent agent execution.
    """

    def __init__(self):
        self._metrics: Dict[str, ProviderMetrics] = {}
        self._lock = threading.Lock()

    def record_success(self, provider: str) -> None:
        """Record a successful provider call."""
        with self._lock:
            if provider not in self._metrics:
                self._metrics[provider] = ProviderMetrics()
            self._metrics[provider].successes += 1

    def record_error(self, provider: str, category: ErrorCategory, retried: bool = False) -> None:
        """Record a provider error.

        Args:
            provider: Provider identifier (e.g., from KRYTH_BASE_URL)
            category: Classified error category
            retried: Whether this error occurred during a retry attempt
        """
        with self._lock:
            if provider not in self._metrics:
                self._metrics[provider] = ProviderMetrics()
            metrics = self._metrics[provider]
            metrics.failures += 1
            metrics.last_error = category
            metrics.last_error_time = time.time()
            if is_provider_failure(category):
                metrics.provider_errors += 1

    def get_metrics(self, provider: str) -> Optional[ProviderMetrics]:
        """Get metrics for a specific provider."""
        with self._lock:
            return self._metrics.get(provider)

    def is_healthy(self, provider: str, threshold: float = 0.95) -> bool:
        """Check if a provider is considered healthy based on success rate."""
        metrics = self.get_metrics(provider)
        if metrics is None:
            return True  # No data means assume healthy
        if metrics.total_requests < 10:
            return True  # Not enough data
        return metrics.success_rate >= threshold

    def all_providers(self) -> Dict[str, ProviderMetrics]:
        """Get metrics for all tracked providers."""
        with self._lock:
            return dict(self._metrics)


# Global singleton for provider health tracking (used by scheduler and ops center)
# This is the module-level instance that ops_center expects.
_provider_health: Optional[ProviderHealth] = None
try:
    _provider_health = ProviderHealth()
except Exception:
    _provider_health = None


def classify_error(error: Exception | str) -> ErrorCategory:
    """Classify an error into a provider-related category.

    Covers finish_reason values from agent_loop LoopResult, OpenAI SDK
    exception names, OS-level network errors (WinError 10054, connection
    reset), and provider HTTP status codes.

    Args:
        error: Exception object or error string

    Returns:
        ErrorCategory enum value
    """
    # Normalize to string + type name
    if isinstance(error, Exception):
        error_str = str(error)
        error_type = type(error).__name__
    else:
        error_str = str(error)
        error_type = ""

    error_lower = error_str.lower()
    type_lower = error_type.lower()

    # ── Exact finish_reason / status string matches (fast path) ──────────────
    _EXACT_MAP = {
        "timeout":           ErrorCategory.TIMEOUT,
        "rate_limit":        ErrorCategory.RATE_LIMIT,
        "payload_too_large": ErrorCategory.PAYLOAD_TOO_LARGE,
        "malformed":         ErrorCategory.MALFORMED,
        "provider_error":    ErrorCategory.PROVIDER,
        "api_error":         ErrorCategory.API_ERROR,
        "interrupted":       ErrorCategory.INTERRUPTED,
        "max_turns":         ErrorCategory.MAX_TURNS,
        "stream_error":      ErrorCategory.MALFORMED,
        "context_overflow":  ErrorCategory.PAYLOAD_TOO_LARGE,
    }
    # Check both the string value and exception type name
    for token, category in _EXACT_MAP.items():
        if error_lower == token or type_lower == token:
            return category
    # Substring match for finish_reason embedded in longer messages
    for token, category in _EXACT_MAP.items():
        if token in error_lower:
            return category

    # ── OpenAI SDK exception type names (check both type name and the string) ──
    # When an exception type name is passed as a string (e.g. "RateLimitError"),
    # error_type is "" but error_lower contains the name.
    _type_check = type_lower if type_lower else error_lower
    if _type_check in ("apitimeouterror", "readtimeout", "connecttimeout",
                       "apitimeout", "httptimeout", "timeouterror"):
        return ErrorCategory.TIMEOUT
    if _type_check in ("ratelimiterror", "ratelimit"):
        return ErrorCategory.RATE_LIMIT
    if _type_check in ("internalservererror", "servererror"):
        return ErrorCategory.PROVIDER
    if _type_check in ("apiconnectionerror", "connectionerror", "connectionrefusederror"):
        # Connection errors may be network or provider — treat as PROVIDER
        return ErrorCategory.PROVIDER
    if _type_check in ("badrequest", "badrequesterror"):
        return ErrorCategory.PAYLOAD_TOO_LARGE

    # ── OS/network errors (Windows WinError 10054, ECONNRESET, etc.) ─────────
    if any(pattern in error_lower for pattern in [
        "winerror 10054", "connection reset by peer", "connection reset",
        "connection aborted", "remotedisconnected", "remote end closed",
        "broken pipe", "errno 104", "errno 111", "errno 10054",
        "connection refused", "network unreachable",
    ]):
        return ErrorCategory.PROVIDER

    # ── Pattern-based classification ──────────────────────────────────────────
    if any(pattern in error_lower for pattern in [
        "timeout", "timed out", "connection dropped", "read timeout",
        "connect timeout", "request timeout", "operation timed out",
    ]):
        return ErrorCategory.TIMEOUT

    if any(pattern in error_lower for pattern in [
        "rate limit", "too many requests", "429", "quota exceeded",
        "concurrent request", "throttle", "requests per minute",
        "tokens per minute", "tpm", "rpm",
    ]):
        return ErrorCategory.RATE_LIMIT

    if any(pattern in error_lower for pattern in [
        "malformed", "invalid chunk", "stream error", "parse error",
        "unexpected token", "invalid json", "json decode", "jsondecodeerror",
        "decode error", "chunk decode", "sseerror", "sse error",
    ]):
        return ErrorCategory.MALFORMED

    if any(pattern in error_lower for pattern in [
        "payload too large", "request entity too large", "413",
        "max tokens", "context length", "context_length_exceeded",
        "input too long", "prompt too long", "tokens per minute",
        "reduce your message size", "request too large",
    ]):
        return ErrorCategory.PAYLOAD_TOO_LARGE

    if any(pattern in error_lower for pattern in [
        "provider", "service unavailable", "gateway", "5xx",
        "500", "502", "503", "504",
        "internal server error", "bad gateway",
        "service error", "upstream", "overloaded",
        "server error", "model unavailable",
    ]):
        return ErrorCategory.PROVIDER

    if any(pattern in error_lower for pattern in [
        "authentication", "401", "403", "api key", "unauthorized",
        "permission denied", "not found", "404",
    ]):
        return ErrorCategory.API_ERROR

    return ErrorCategory.UNKNOWN


def is_provider_failure(category: ErrorCategory) -> bool:
    """Determine if an error category represents a transient provider failure.

    Provider failures are retryable at the agent level and should not
    cause the team to be marked as FAILED.

    INTERRUPTED and MAX_TURNS are NOT provider failures — they are
    agent-lifecycle events. API_ERROR covers auth/quota issues that are
    not automatically retryable.

    Args:
        category: Error category to check

    Returns:
        True if this is a provider-side failure (vs. agent code error)
    """
    return category in {
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
        ErrorCategory.MALFORMED,
        ErrorCategory.PROVIDER,
        ErrorCategory.PAYLOAD_TOO_LARGE,
    }


