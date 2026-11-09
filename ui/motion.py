"""Shared reduced-motion policy for startup and live terminal indicators."""

from __future__ import annotations

import os


_TRUE = {"1", "true", "yes", "on"}


def animations_enabled(config: object) -> bool:
    ui = getattr(config, "ui", None)
    configured = bool(getattr(ui, "animations", True))
    reduced_motion = os.environ.get("KRYTH_REDUCED_MOTION", "").lower() in _TRUE
    disabled = os.environ.get("KRYTH_NO_ANIMATION", "").lower() in _TRUE
    return configured and not reduced_motion and not disabled
