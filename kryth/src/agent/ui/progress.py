"""KRYTH progress indicators."""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from agent.ui.console import console
from agent.ui.theme import CORE


# ── Braille spinner frames ────────────────────────────────────────────────────
_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_GOLD   = "\033[38;2;232;255;58m"
_MUTED  = "\033[38;2;136;136;136m"
_RESET  = "\033[0m"
_CLEAR  = "\r\033[K"   # carriage return + erase to end of line


def _wi(s: str) -> None:
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except Exception:
        pass


class Spinner:
    """Thread-driven Braille spinner.

    Writes directly to stdout so it works on every terminal regardless of
    Rich's is_terminal detection. The _console_lock prevents interleaving
    with concurrent console.print() calls.
    """

    # Shared lock used by Spinner AND the console wrapper so they don't race.
    _console_lock: threading.Lock = threading.Lock()

    def __init__(self, message: str = "Working") -> None:
        self._message = message
        self._active = False
        self._thread: threading.Thread | None = None
        self._frame = 0
        self._lock = threading.Lock()
        self._last_len = 0  # chars written on current spinner line

    def start(self, message: str | None = None) -> None:
        with self._lock:
            if self._active:
                return
            if message is not None:
                self._message = message
            self._active = True
            self._frame = 0
            t = threading.Thread(target=self._spin, daemon=True)
            self._thread = t
        t.start()

    def update(self, message: str) -> None:
        with self._lock:
            self._message = message

    def stop(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
        thread = self._thread
        if thread:
            thread.join(timeout=0.4)
        with Spinner._console_lock:
            _wi(_CLEAR)
        self._last_len = 0
        self._thread = None

    def _spin(self) -> None:
        while True:
            with self._lock:
                if not self._active:
                    break
                frame = _FRAMES[self._frame % len(_FRAMES)]
                self._frame += 1
                msg = self._message
            line = f"{_GOLD}{frame}{_RESET}  {_MUTED}{msg[:88]}{_RESET}"
            with Spinner._console_lock:
                _wi(f"{_CLEAR}{line}")
            time.sleep(0.1)
        with Spinner._console_lock:
            _wi(_CLEAR)

    def __enter__(self) -> "Spinner":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


@contextmanager
def progress(description: str) -> Iterator[Progress]:
    p = Progress(
        TextColumn("[kryth.core]◈[/kryth.core] [muted]{task.description}[/muted]"),
        BarColumn(
            complete_style="kryth.core",
            finished_style="log.success",
            pulse_style="kryth.core.dim",
        ),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with p:
        p.add_task(description, total=None)
        yield p
