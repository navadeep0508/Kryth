"""Activity indicator: single-source spinner state machine.

Before: every event handler in ``renderer.py`` called ``_spinner.stop()``
defensively, which raced on edge cases (chunk arriving before the
WAITING event, error during streaming, compaction overlap with the
content stream). The spinner sometimes vanished mid-stream or stayed
visible under printed text.

After: every visible activity transitions through this module. The
event handlers say "we're waiting" / "tokens are flowing" /
"compacting" — they don't poke ``Spinner.start/stop`` directly. The
state machine decides whether the spinner is visible and what label
it carries.

States:

    IDLE         no spinner
    WAITING      "waiting for model…"  (pre-first-token)
    STREAMING    no spinner (the stream itself is the indicator)
    COMPACTING   "compacting context…"

Transitions are idempotent — entering a state you're already in is a
no-op, so chunk events don't thrash the spinner.
"""

from __future__ import annotations

from typing import Literal

from agent.ui.progress import Spinner


State = Literal["idle", "waiting", "streaming", "compacting"]


class ActivityIndicator:
    def __init__(self) -> None:
        self._state: State = "idle"
        self._spinner = Spinner()

    @property
    def state(self) -> State:
        return self._state

    # -- entry points -------------------------------------------------

    def idle(self) -> None:
        self._transition("idle")

    def waiting(self, message: str = "◈ Surveying repository...") -> None:
        self._transition("waiting", message)

    def streaming(self) -> None:
        # Kill the spinner the instant tokens start flowing. The stream
        # itself is the activity indicator from here on.
        self._transition("streaming")

    def compacting(self, message: str = "◈ Synchronizing memory...") -> None:
        self._transition("compacting", message)

    # -- internals ----------------------------------------------------

    def _transition(self, target: State, message: str | None = None) -> None:
        if target == self._state:
            # Re-entering the same state with a fresh message refreshes
            # the spinner label without restarting it.
            if message is not None and target in ("waiting", "compacting"):
                self._spinner.update(message)
            return

        prev = self._state
        self._state = target

        if target in ("waiting", "compacting"):
            if prev in ("waiting", "compacting"):
                # Already showing a spinner; just relabel.
                if message is not None:
                    self._spinner.update(message)
            else:
                self._spinner.start(message)
            return

        # idle / streaming: spinner off.
        self._spinner.stop()


__all__ = ["ActivityIndicator", "State"]
