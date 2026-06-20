"""Timing, structured metrics, and JSON-line logging."""

import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field


# ── Logger setup ──────────────────────────────────────────────────────────────

def setup_logging(level: str = "INFO") -> logging.Logger:
    """Return a JSON-line logger writing to stdout."""
    logger = logging.getLogger("nim_router")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JSONFormatter())
        logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level":  record.levelname,
        }
        if isinstance(record.msg, dict):
            base.update(record.msg)
        else:
            base["msg"] = record.getMessage()
        return json.dumps(base, default=str)


# ── Timer ─────────────────────────────────────────────────────────────────────

class Timer:
    """High-resolution wall-clock with first-token marking."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._first_token: float | None = None

    def mark_first_token(self) -> None:
        if self._first_token is None:
            self._first_token = time.perf_counter()

    @property
    def ttft_ms(self) -> float:
        if self._first_token is None:
            return 0.0
        return (self._first_token - self._start) * 1000.0

    @property
    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0


# ── Metrics dataclass ─────────────────────────────────────────────────────────

@dataclass
class RequestMetrics:
    """All captured data for a single routed request."""
    role: str
    models_attempted: list[str] = field(default_factory=list)
    model_selected: str = ""
    fallback_level: int = 0
    ttft_ms: float = 0.0
    total_latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    errors: list[dict] = field(default_factory=list)
    success: bool = False

    def log(self, logger: logging.Logger) -> None:
        logger.info({
            "event":             "request_complete",
            "role":              self.role,
            "model_selected":    self.model_selected,
            "fallback_level":    self.fallback_level,
            "ttft_ms":           round(self.ttft_ms, 2),
            "total_latency_ms":  round(self.total_latency_ms, 2),
            "prompt_tokens":     self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens":      self.total_tokens,
            "success":           self.success,
            "errors":            self.errors,
        })
