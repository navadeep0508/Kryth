"""Environment helpers for KRYTH AI Coder.

Public configuration uses ``KRYTH_*`` names. The previous
``XEROCODEAI_*`` variables are still accepted so existing installs keep
working.
"""

from __future__ import annotations

import os
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "y", "on"}


ALIASES: dict[str, tuple[str, ...]] = {
    "KRYTH_HOME": ("AICODER_HOME", "XEROCODEAI_HOME"),
    "KRYTH_BASE_URL": ("AICODER_BASE_URL", "XEROCODEAI_BASE_URL"),
    "KRYTH_MAIN_MODEL": ("AICODER_MAIN_MODEL", "XEROCODEAI_MAIN_MODEL"),
    "KRYTH_PLANNER_MODEL": ("AICODER_PLANNER_MODEL", "XEROCODEAI_PLANNER_MODEL"),
    "KRYTH_SUMMARIZER_MODEL": ("AICODER_SUMMARIZER_MODEL", "XEROCODEAI_SUMMARIZER_MODEL"),
    # New role-specific model env vars
    "KRYTH_VISION_MODEL": ("AICODER_VISION_MODEL",),
    "KRYTH_EXTRACTION_MODEL": ("AICODER_EXTRACTION_MODEL",),
    "KRYTH_REASONING_MODEL": ("AICODER_REASONING_MODEL",),
    "KRYTH_NVIDIA_BASE_URL": ("AICODER_NVIDIA_BASE_URL",),
    "KRYTH_VALIDATE_MODELS": ("AICODER_VALIDATE_MODELS",),
    "KRYTH_PROFILE": ("AICODER_PROFILE", "XEROCODEAI_PROFILE"),
    "KRYTH_ASSUME_YES": ("AICODER_ASSUME_YES", "XEROCODEAI_ASSUME_YES"),
    "KRYTH_NO_PERSIST": ("AICODER_NO_PERSIST", "XEROCODEAI_NO_PERSIST"),
    "KRYTH_NO_RESUME": ("AICODER_NO_RESUME", "XEROCODEAI_NO_RESUME"),
    "KRYTH_AUTO_ROUTE": ("AICODER_AUTO_ROUTE", "XEROCODEAI_AUTO_ROUTE"),
    "KRYTH_ROUTE_SMALL_THRESHOLD": ("AICODER_ROUTE_SMALL_THRESHOLD", "XEROCODEAI_ROUTE_SMALL_THRESHOLD"),
    "KRYTH_LOG_LEVEL": ("AICODER_LOG_LEVEL", "XEROCODEAI_LOG_LEVEL"),
    "KRYTH_LOG_DIR": ("AICODER_LOG_DIR", "XEROCODEAI_LOG_DIR"),
    "KRYTH_FORCE_COLOR": ("AICODER_FORCE_COLOR", "XEROCODEAI_FORCE_COLOR"),
    "KRYTH_NO_COLOR": ("AICODER_NO_COLOR", "XEROCODEAI_NO_COLOR"),
    "KRYTH_NO_MOTION": ("AICODER_NO_MOTION", "XEROCODEAI_NO_MOTION"),
    "KRYTH_CINEMATIC": ("AICODER_CINEMATIC", "XEROCODEAI_CINEMATIC"),
    "KRYTH_BRIDGE_PORT": ("AICODER_BRIDGE_PORT", "XEROCODEAI_BRIDGE_PORT"),
    "KRYTH_BRIDGE_PROVIDER": ("AICODER_BRIDGE_PROVIDER", "XEROCODEAI_BRIDGE_PROVIDER"),
    "KRYTH_BRIDGE_HEADLESS": ("AICODER_BRIDGE_HEADLESS", "XEROCODEAI_BRIDGE_HEADLESS"),
    "KRYTH_TOOL_MODE": ("AICODER_TOOL_MODE", "XEROCODEAI_TOOL_MODE"),
}


def getenv(name: str, default: str = "") -> str:
    """Read ``name`` with support for documented legacy aliases."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    for alias in ALIASES.get(name, ()):
        value = os.environ.get(alias, "").strip()
        if value:
            return value
    return default


def getenv_bool(name: str, default: bool = False) -> bool:
    value = getenv(name)
    if not value:
        return default
    return value.strip().lower() in TRUE_VALUES


def getenv_int(name: str, default: int) -> int:
    try:
        return int(getenv(name))
    except (TypeError, ValueError):
        return default


def setenv(name: str, value: str) -> None:
    """Set the canonical name and any legacy aliases used in-process."""
    os.environ[name] = value
    for alias in ALIASES.get(name, ()):
        os.environ[alias] = value


def home_dir() -> Path:
    return Path(getenv("KRYTH_HOME")) if getenv("KRYTH_HOME") else Path.home() / ".kryth"


__all__ = [
    "ALIASES",
    "TRUE_VALUES",
    "getenv",
    "getenv_bool",
    "getenv_int",
    "home_dir",
    "setenv",
]
