"""KRYTH progress indicators."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.status import Status
from rich.text import Text

from agent.ui.console import console
from agent.ui.theme import CORE, WAITING


class Spinner:
    """Transient waiting indicator using KRYTH symbols."""

    def __init__(self, message: str = "Waiting") -> None:
        self._status: Status | None = None
        self._message = message

    def start(self, message: str | None = None) -> None:
        if self._status is not None or not console.is_terminal:
            return
        label = message or self._message
        self._status = console.status(
            Text.assemble((WAITING, "muted"), (" " + label, "muted")),
            spinner="dots",
            spinner_style="kryth.core",
        )
        self._status.start()

    def update(self, message: str) -> None:
        self._message = message
        if self._status is not None:
            self._status.update(Text.assemble((CORE, "kryth.core"), (" " + message, "muted")))

    def stop(self) -> None:
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

    def __exit__(self, *a) -> None:
        self.stop()


@contextmanager
def progress(description: str) -> Iterator[Progress]:
    p = Progress(
        TextColumn("[kryth.core]◈[/kryth.core] [muted]{task.description}[/muted]"),
        BarColumn(complete_style="kryth.core", finished_style="log.success", pulse_style="kryth.core.dim"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    with p:
        p.add_task(description, total=None)
        yield p
