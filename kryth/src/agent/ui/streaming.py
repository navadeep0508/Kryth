"""Streaming renderer for KRYTH responses."""

from __future__ import annotations

import sys
import time

from agent.ui.console import console
from agent.ui.motion import motion_enabled, sleep, DIAMOND_THINKING_FRAMES
from agent.ui.theme import CORE

# Parallel build mode: when True, the "Evaluating..." spinner is suppressed
# to avoid garbled output when multiple agents run concurrently.
_parallel_mode = False

def set_parallel_mode(enabled: bool) -> None:
    """Enable or disable parallel mode for the streaming output."""
    global _parallel_mode
    _parallel_mode = enabled

# Acid gold in ANSI — matches kryth.core (#E8FF3A)
_ANSI_GOLD = "\033[38;2;232;255;58m"
_ANSI_MUTED = "\033[38;2;136;136;136m"
_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"

# Sine-wave brightness levels for the label (simulates a pulse)
_PULSE_STYLES = [
    "\033[38;2;136;136;136m",  # dim
    "\033[38;2;180;180;180m",  # mid-dim
    "\033[38;2;220;220;220m",  # mid
    "\033[38;2;255;255;255m",  # bright
    "\033[38;2;220;220;220m",  # mid
    "\033[38;2;180;180;180m",  # mid-dim
]


def _flush_threshold() -> int:
    try:
        cols = console.size.width
    except Exception:
        cols = 80
    return max(40, min(cols, 200))


def _write_inplace(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass


class StreamPrinter:
    def __init__(self) -> None:
        self._reasoning_started = False
        self._content_started = False
        self._buf: list[str] = []
        self._buf_len = 0
        self._reasoning_frame = 0
        self._pulse_frame = 0

    def _emit(self, text: str, *, style: str | None = None) -> None:
        if not text:
            return
        console.out(text, end="", highlight=False, style=style)
        if motion_enabled():
            if text.endswith((".", ":", "?", "!", "\n")):
                sleep(0.012)
            elif len(text) < 8:
                sleep(0.003)

    def _flush(self) -> None:
        if self._buf_len == 0:
            return
        self._emit("".join(self._buf))
        self._buf = []
        self._buf_len = 0

    def _ingest(self, piece: str) -> None:
        if not piece:
            return
        threshold = _flush_threshold()
        if "\n" not in piece and self._buf_len + len(piece) < threshold:
            self._buf.append(piece)
            self._buf_len += len(piece)
            return

        chunks = piece.split("\n")
        for part in chunks[:-1]:
            self._buf.append(part + "\n")
            self._buf_len += len(part) + 1
            self._flush()
        tail = chunks[-1]
        if tail:
            self._buf.append(tail)
            self._buf_len += len(tail)
        if self._buf_len >= threshold:
            self._soft_flush()

    def _soft_flush(self) -> None:
        joined = "".join(self._buf)
        cut = joined.rfind(" ")
        if cut <= 0:
            self._flush()
            return
        self._emit(joined[:cut + 1])
        rest = joined[cut + 1:]
        self._buf = [rest] if rest else []
        self._buf_len = len(rest)

    def begin_reasoning(self) -> None:
        if self._reasoning_started:
            return
        self._reasoning_started = True
        if _parallel_mode:
            # In parallel mode, skip spinner output to avoid garbling.
            self._reasoning_frame = 0
            self._pulse_frame = 0
            return
        self._reasoning_frame = 0
        self._pulse_frame = 0
        glyph = DIAMOND_THINKING_FRAMES[0]
        label_style = _PULSE_STYLES[0]
        _write_inplace(
            f"\n{_ANSI_BOLD}{_ANSI_GOLD}{glyph}{_ANSI_RESET} "
            f"{label_style}Evaluating...{_ANSI_RESET}"
        )

    def reasoning_chunk(self, piece: str, elapsed: float = 0.0) -> None:
        if not self._reasoning_started:
            self.begin_reasoning()
        if not _parallel_mode:
            self._reasoning_frame = (self._reasoning_frame + 1) % len(DIAMOND_THINKING_FRAMES)
            self._pulse_frame = (self._pulse_frame + 1) % len(_PULSE_STYLES)
            glyph = DIAMOND_THINKING_FRAMES[self._reasoning_frame]
            label_style = _PULSE_STYLES[self._pulse_frame]
            _write_inplace(
                f"\r{_ANSI_BOLD}{_ANSI_GOLD}{glyph}{_ANSI_RESET} "
                f"{label_style}Evaluating...{_ANSI_RESET}"
            )
        if motion_enabled():
            sleep(0.06)
        del piece

    def end_reasoning(self) -> None:
        if self._reasoning_started:
            if not _parallel_mode:
                _write_inplace(f"\r{' ' * 20}\r")
            self._reasoning_started = False

    def begin_content(self) -> None:
        if self._content_started:
            return
        if self._reasoning_started:
            self.end_reasoning()
        self._content_started = True
        console.print(
            f"[role.assistant]{CORE} KRYTH[/role.assistant] ",
            end="",
        )

    def content_chunk(self, piece: str) -> None:
        if not self._content_started:
            self.begin_content()
        self._ingest(piece)

    def end_content(self, *, render_markdown: bool = True) -> None:
        del render_markdown
        if not self._content_started:
            return
        self._flush()
        console.out("\n", end="", highlight=False)
        self._content_started = False

    def force_newline(self) -> None:
        if self._reasoning_started:
            self.end_reasoning()
        if self._content_started:
            self._flush()
            console.out("\n", end="", highlight=False)
            self._content_started = False


