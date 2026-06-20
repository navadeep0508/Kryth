"""Type definitions shared across the router."""

from dataclasses import dataclass, field
from enum import Enum


class ModelRole(str, Enum):
    MAIN       = "main"
    PLANNER    = "planner"
    SUMMARIZER = "summarizer"
    VISION     = "vision"


@dataclass
class RouteResult:
    """Successful result of a routed LLM call."""
    role: str
    model_used: str
    fallback_level: int       # 0 = primary, 1 = fallback1, 2 = fallback2
    content: str
    ttft_ms: float            # time to first token (ms)
    total_latency_ms: float   # end-to-end wall clock (ms)
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    errors: list[dict] = field(default_factory=list)  # errors from skipped models
