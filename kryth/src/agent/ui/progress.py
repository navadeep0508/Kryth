"""KRYTH progress indicators."""

from __future__ import annotations

import re as _re
import time as _time
from contextlib import contextmanager
from typing import Iterator

from rich.status import Status
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, WAITING


def _norm_label(s: str) -> str:
    """Normalize a spinner label: the braille spinner + ◈ glyph are drawn by
    the Spinner itself, so strip any leading brand glyph a caller prepended and
    collapse trailing ellipsis variants to a single … (avoids '◈  ◈ msg……')."""
    s = (s or "").strip()
    while s[:1] in ("◈", "◇", "◆"):
        s = s[1:].lstrip()
    s = _re.sub(r"[.…]{2,}$", "…", s)
    return s


class Spinner:
    """Rich-based status spinner with true in-place animation."""

    def __init__(self, message: str = "Working") -> None:
        self._status: Status | None = None
        self._message = message
        self._start_time: float | None = None

    def start(self, message: str | None = None) -> None:
        if self._status is not None:
            return
        label = _norm_label(message or self._message)
        self._start_time = _time.monotonic()
        try:
            self._status = console.status(
                Text.assemble((CORE, "kryth.core"), ("  " + label, "muted")),
                spinner="dots",
                spinner_style="kryth.core",
            )
            self._status.start()
        except Exception:
            pass

    def update(self, message: str) -> None:
        self._message = message
        if self._status is not None:
            try:
                self._status.update(
                    Text.assemble((CORE, "kryth.core"), ("  " + _norm_label(message), "muted"))
                )
            except Exception:
                pass

    def stop(self, final_message: str | None = None) -> None:
        if self._status is None:
            return
        try:
            self._status.stop()
        except Exception:
            pass
        finally:
            self._status = None

    def __enter__(self) -> "Spinner":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()


@contextmanager
def progress(description: str) -> Iterator[Progress]:
    from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

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