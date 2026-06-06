"""Type-safe configuration schema for KRYTH's model configuration system.

Supports two modes:
  unified    — one model for everything (beginner-friendly)
  specialized — per-role models for cost/performance optimization
"""

from __future__ import annotations

import os
import re
from typing import Any, Literal

try:
    from pydantic import BaseModel, field_validator, model_validator
    _PYDANTIC = True
except ImportError:
    _PYDANTIC = False


def _resolve_env(value: str) -> str:
    """Expand ${VAR_NAME} placeholders from environment."""
    def _repl(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{([^}]+)\}", _repl, value)


# ---------------------------------------------------------------------------
# Dataclass fallback (if pydantic is not installed)
# ---------------------------------------------------------------------------

if not _PYDANTIC:
    from dataclasses import dataclass, field

    @dataclass
    class ProviderConfig:
        api_key: str = ""
        base_url: str = ""
        timeout: float = 60.0
        max_retries: int = 3
        headers: dict = field(default_factory=dict)
        proxy: str = ""

        def __post_init__(self):
            self.api_key = _resolve_env(self.api_key)
            self.base_url = _resolve_env(self.base_url)

    @dataclass
    class ModelSpec:
        provider: str = "openai"
        model: str = ""
        fallbacks: list = field(default_factory=list)

    @dataclass
    class KrythConfig:
        mode: str = "unified"
        providers: dict = field(default_factory=dict)
        provider: str = ""
        model: str = ""
        base_url: str = ""
        api_key: str = ""
        models: dict = field(default_factory=dict)
        routing: dict = field(default_factory=dict)
        fallbacks: dict = field(default_factory=dict)

else:
    class ProviderConfig(BaseModel):
        """Configuration for a single API provider."""
        api_key: str = ""
        base_url: str = ""
        timeout: float = 60.0
        max_retries: int = 3
        headers: dict[str, str] = {}
        proxy: str = ""

        @field_validator("api_key", "base_url", mode="before")
        @classmethod
        def _resolve(cls, v: Any) -> str:
            return _resolve_env(str(v)) if v else ""

    class ModelSpec(BaseModel):
        """Configuration for a single model role."""
        provider: str = "openai"
        model: str = ""
        fallbacks: list[str] = []

    class KrythConfig(BaseModel):
        """Root configuration object for KRYTH's model system."""
        mode: Literal["unified", "specialized"] = "unified"

        # ---- Provider registry (specialized mode) ----
        providers: dict[str, ProviderConfig] = {}

        # ---- Unified mode fields ----
        provider: str = ""
        model: str = ""
        base_url: str = ""
        api_key: str = ""

        # ---- Specialized mode fields ----
        models: dict[str, ModelSpec] = {}
        routing: dict[str, str] = {}
        fallbacks: dict[str, list[str]] = {}

        @field_validator("api_key", "base_url", mode="before")
        @classmethod
        def _resolve(cls, v: Any) -> str:
            return _resolve_env(str(v)) if v else ""

        @model_validator(mode="after")
        def _normalize(self) -> "KrythConfig":
            # If providers dict has an inline "api_key" (unified shorthand),
            # resolve it into the unified api_key field.
            if self.api_key:
                self.api_key = _resolve_env(self.api_key)
            return self


# ---------------------------------------------------------------------------
# Role constants
# ---------------------------------------------------------------------------

ROLES = ["main", "planner", "vision", "summary", "extraction", "reasoning"]

ROLE_DESCRIPTIONS = {
    "main":       "General conversation, coding, autonomous execution",
    "planner":    "Task breakdown, replanning, workflow generation",
    "vision":     "Screenshots, browser vision, UI understanding, OCR",
    "summary":    "Conversation compression, memory summarization",
    "extraction": "Structured JSON extraction, HTML parsing, document extraction",
    "reasoning":  "Debugging, complex planning, multi-step analysis",
}

# Default NVIDIA vision model (used when no vision config is provided)
DEFAULT_VISION_MODEL = "stepfun-ai/step-3.7-flash"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
