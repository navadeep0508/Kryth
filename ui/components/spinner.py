from __future__ import annotations

import asyncio

from typing import Any, Callable

from rich.console import Console
from rich.live import Live

SPINNER_FRAMES = [".", "o", "O", "o"]
SPINNER_INTERVAL = 0.04

REASONING_FRAMES = [".", "o", "*", "o"]
REASONING_SPEED = 0.25


class Spinner:
    """Owns the `Live` region and the task that advances its frame.

    Callers hand in a render callback and get the frame counter back through
    `frame`, so they can drive their own animations off the same clock.
    """

    def __init__(self, console: Console, *, enabled: bool = True) -> None:
        self.console = console
        self.enabled = enabled
        self.frame = 0
        self._live: Live | None = None
        self._task: asyncio.Task | None = None
        self._render: Callable[[], Any] | None = None

    @property
    def render(self) -> Callable[[], Any] | None:
        """The current render callback, so a caller can restore it later."""

        return self._render

    @property
    def running(self) -> bool:
        return self._live is not None

    def char(self) -> str:
        return SPINNER_FRAMES[self.frame % len(SPINNER_FRAMES)]

    def reasoning_char(self) -> str:
        """The star twinkles slower than the tool spinner, so it reads as a pulse."""

        frame = int(self.frame * REASONING_SPEED)
        return REASONING_FRAMES[frame % len(REASONING_FRAMES)]

    def start(self, render: Callable[[], Any]) -> None:
        self.stop()
        self.frame = 0
        self._render = render
        if not self.enabled:
            self.console.print(render())
            return
        self._live = Live(
            render(),
            console=self.console,
            refresh_per_second=1 / SPINNER_INTERVAL,
            transient=True,
        )
        self._live.start()
        self._task = asyncio.create_task(self._animate())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._live is not None:
            self._live.stop()
            self._live = None
        self._render = None

    async def _animate(self) -> None:
        try:
            while True:
                await asyncio.sleep(SPINNER_INTERVAL)
                self.frame += 1
                if self._live is not None and self._render is not None:
                    self._live.update(self._render())
        except asyncio.CancelledError:
            pass
