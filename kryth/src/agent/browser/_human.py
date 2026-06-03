"""Standalone human-like behavior flag.

Kept in its own module to break circular imports — no imports from
agent.browser submodules, only os and typing.
"""

from __future__ import annotations

import os

_HUMAN_LIKE = os.environ.get("KRYTH_HUMAN_LIKE", "0") == "1"


def is_human_like() -> bool:
    """Check if human-like behavior (random pauses, natural mouse movement)
    is enabled via the KRYTH_HUMAN_LIKE environment variable."""
    return _HUMAN_LIKE
