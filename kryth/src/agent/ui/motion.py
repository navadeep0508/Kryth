"""Subtle terminal motion primitives for KRYTH.

Motion communicates state only. No flashy effects, no rainbow palettes.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, WAITING, ERROR


NO_MOTION = os.environ.get(
    "KRYTH_NO_MOTION",
    os.environ.get("AICODER_NO_MOTION", ""),
).lower() in {"1", "true", "yes", "on"}
CINEMATIC = os.environ.get(
    "KRYTH_CINEMATIC",
    os.environ.get("AICODER_CINEMATIC", "1"),
).lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class MotionSpec:
    name: str
    frame_delay: float
    enter_ms: int
    settle_ms: int


NEURAL_SPEC = MotionSpec("kryth", 0.035, 120, 60)
SWIFT_SPEC = MotionSpec("swift", 0.018, 70, 30)
CINEMA_SPEC = MotionSpec("calm", 0.045, 160, 80)

PULSE_FRAMES: tuple[str, ...] = (WAITING, CORE, CORE, CORE)
WAVE_FRAMES: tuple[str, ...] = (WAITING, CORE, CORE + CORE, CORE + CORE + CORE)
ORBIT_FRAMES: tuple[str, ...] = (WAITING, CORE, ERROR, CORE)

# KRYTH v1 Animation Frames
DIAMOND_BOOT_FRAMES: tuple[str, ...] = (
    "            ◇",
    "          ◇ ◇ ◇",
    "        ◇ ◈ ◈ ◇",
    "      ◇ ◈ ◈ ◈ ◇",
    "            ◈",
)
DIAMOND_THINKING_FRAMES: tuple[str, ...] = (WAITING, CORE, ERROR, CORE)
DIAMOND_PULSE_FRAMES: tuple[str, ...] = (WAITING, CORE, CORE, CORE)
DIAMOND_WAVE_FRAMES: tuple[str, ...] = ("◇", "◇◈", "◇◈◈", "◈◈◈", "◈◈", "◈")

# KRYTH ASCII Art
KRYTH_ASCII_ART = """██╗  ██╗██████╗ ██╗   ██╗████████╗██╗  ██╗
██║ ██╔╝██╔══██╗╚██╗ ██╔╝╚══██╔══╝██║  ██║
█████╔╝ ██████╔╝ ╚████╔╝    ██║   ███████║
██╔═██╗ ██╔══██╗  ╚██╔╝     ██║   ██╔══██║
██║  ██╗██║  ██║   ██║      ██║   ██║  ██║
╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝      ╚═╝   ╚═╝  ╚═╝"""


def motion_enabled() -> bool:
    return bool(CINEMATIC and not NO_MOTION and console.is_terminal)


def sleep(seconds: float) -> None:
    if motion_enabled() and seconds > 0:
        time.sleep(seconds)


def gradient_text(text: str, *, styles: Sequence[str] | None = None, bold: bool = False) -> Text:
    """Return brand text without rainbow gradients.

    The function name is kept for compatibility with existing callers.
    """
    del styles
    return Text(text, style="kryth.core" if bold else "accent")


def shimmer_text(text: str, *, highlight: str = "kryth.core") -> Text:
    out = Text()
    for i, char in enumerate(text):
        out.append(char, style=highlight if i % 5 == 0 and not char.isspace() else "muted")
    return out


def pulse_badge(label: str, *, frame: int = 0, style: str = "kryth.core") -> Text:
    glyph = PULSE_FRAMES[frame % len(PULSE_FRAMES)]
    return Text.assemble((glyph, style), " ", (label, "title"))


def status_chip(label: str, value: str, *, style: str = "kryth.core") -> Text:
    return Text.assemble((label.upper(), "muted"), " ", (value, style))


def reveal_lines(lines: Iterable[Text | str], *, delay: float = 0.018) -> None:
    for line in lines:
        console.print(line)
        sleep(delay)


def frame_cycle(frames: Sequence[str], count: int) -> Iterator[str]:
    for i in range(count):
        yield frames[i % len(frames)]


def truncate_middle(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    half = (max_len - 1) // 2
    return text[:half] + "…" + text[-(max_len - half - 1):]


def animate_sequence(frames: Sequence[str], *,
                     delay: float = 0.15,
                     center: bool = True,
                     style: str = "kryth.core") -> None:
    """Display animated frame sequence with optional centering."""
    if not motion_enabled():
        # Show only final frame if motion disabled
        final = frames[-1] if frames else ""
        if center:
            console.print(final, style=style, justify="center")
        else:
            console.print(Text(final, style=style))
        return

    for frame in frames:
        if center:
            console.print(frame, style=style, justify="center")
        else:
            console.print(Text(frame, style=style))
        sleep(delay)


def progress_diamonds(current: int, total: int) -> Text:
    """Diamond-based progress indicator: ◈◈◈◇◇"""
    filled = min(current, total)
    empty = max(0, total - filled)

    result = Text()
    result.append(CORE * filled, style="kryth.core")
    result.append(WAITING * empty, style="muted")
    return result


def render_ascii_art(art: str, style: str = "kryth.core") -> None:
    """Render ASCII art with style and centering."""
    for line in art.split("\n"):
        console.print(line, style=style, justify="center")


__all__ = [
    "CINEMA_SPEC",
    "DIAMOND_BOOT_FRAMES",
    "DIAMOND_PULSE_FRAMES",
    "DIAMOND_THINKING_FRAMES",
    "DIAMOND_WAVE_FRAMES",
    "KRYTH_ASCII_ART",
    "NEURAL_SPEC",
    "NO_MOTION",
    "ORBIT_FRAMES",
    "PULSE_FRAMES",
    "SWIFT_SPEC",
    "WAVE_FRAMES",
    "MotionSpec",
    "animate_sequence",
    "frame_cycle",
    "gradient_text",
    "motion_enabled",
    "progress_diamonds",
    "pulse_badge",
    "render_ascii_art",
    "reveal_lines",
    "shimmer_text",
    "sleep",
    "status_chip",
    "truncate_middle",
]
