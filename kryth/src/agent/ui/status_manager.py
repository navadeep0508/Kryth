"""Status manager: turn timing + status-line composition.

Tracks the elapsed time of the current turn so renderers can show a
running ``1.4s`` next to the status line, and exposes a tiny API for
building the status text from semantic parts.
"""

from __future__ import annotations

import time


_turn_start: float | None = None


def mark_turn_start() -> None:
    global _turn_start
    _turn_start = time.monotonic()


def turn_elapsed() -> float | None:
    return None if _turn_start is None else (time.monotonic() - _turn_start)


def turn_reset() -> None:
    global _turn_start
    _turn_start = None


def build_status_parts(
    *,
    model: str,
    mode: str,
    tokens_in: int,
    tokens_out: int,
) -> list[str]:
    """Return list of styled strings ready for ``components.status_line``."""
    parts = [
        f"[status.model]{model}[/status.model]",
        f"mode [status.mode]{mode}[/status.mode]",
        f"tokens [status.tokens]{tokens_in + tokens_out:,}[/status.tokens] "
        f"[muted](in {tokens_in:,} / out {tokens_out:,})[/muted]",
    ]
    elapsed = turn_elapsed()
    if elapsed is not None:
        parts.append(f"[status.elapsed]{elapsed:.1f}s[/status.elapsed]")
    return parts
