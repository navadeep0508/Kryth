"""Activity indicator: single-source spinner state machine."""

from __future__ import annotations

import threading
from typing import Literal

from agent.ui.progress import Spinner


State = Literal["idle", "waiting", "streaming", "compacting"]

# Cycling suffixes appended to the base message every 3s during long waits.
# Gives the user evidence that KRYTH is still working even on silent operations.
_CYCLE_SUFFIXES = [
    "analyzing…",
    "computing…",
    "processing…",
    "synthesizing…",
    "evaluating…",
    "verifying…",
    "reasoning…",
    "preparing…",
]


class ActivityIndicator:
    def __init__(self) -> None:
        self._state: State = "idle"
        self._spinner = Spinner()
        self._base_message: str = ""
        self._cycle_idx: int = 0
        self._cycle_timer: threading.Timer | None = None
        self._cycle_lock = threading.Lock()

    @property
    def state(self) -> State:
        return self._state

    # -- entry points -------------------------------------------------

    def _mc_active(self) -> bool:
        """True when Mission Control's Live display owns the terminal.
        Lazy import avoids circular dependency (mission_control → activity → mission_control).
        """
        try:
            import sys
            mc_mod = sys.modules.get("agent.ui.mission_control")
            if mc_mod is None:
                return False
            return mc_mod.get_active_mc() is not None
        except Exception:
            return False

    def idle(self) -> None:
        if self._mc_active():
            return
        self._stop_cycler()
        self._transition("idle")

    def waiting(self, message: str = "◈ Surveying repository...") -> None:
        if self._mc_active():
            return
        self._base_message = message
        self._transition("waiting", message)
        self._start_cycler()

    def streaming(self) -> None:
        if self._mc_active():
            return
        self._stop_cycler()
        self._transition("streaming")

    def compacting(self, message: str = "◈ Synchronizing memory...") -> None:
        self._stop_cycler()
        self._transition("compacting", message)

    # -- cycler -------------------------------------------------------

    def _start_cycler(self) -> None:
        with self._cycle_lock:
            if self._cycle_timer is not None:
                return  # already running
            self._cycle_idx = 0
            self._schedule_cycle()

    def _stop_cycler(self) -> None:
        with self._cycle_lock:
            if self._cycle_timer:
                self._cycle_timer.cancel()
                self._cycle_timer = None

    def _schedule_cycle(self) -> None:
        """Called while holding _cycle_lock."""
        t = threading.Timer(3.0, self._on_cycle)
        t.daemon = True
        self._cycle_timer = t
        t.start()

    def _on_cycle(self) -> None:
        with self._cycle_lock:
            if self._cycle_timer is None:
                return  # stopped
            if self._state != "waiting":
                self._cycle_timer = None
                return
            suffix = _CYCLE_SUFFIXES[self._cycle_idx % len(_CYCLE_SUFFIXES)]
            self._cycle_idx += 1
            new_msg = f"{self._base_message}  {suffix}"
            self._spinner.update(new_msg)
            self._schedule_cycle()

    # -- internals ----------------------------------------------------

    def _transition(self, target: State, message: str | None = None) -> None:
        if target == self._state:
            if message is not None and target in ("waiting", "compacting"):
                self._spinner.update(message)
            return

        prev = self._state
        self._state = target

        if target in ("waiting", "compacting"):
            if prev in ("waiting", "compacting"):
                if message is not None:
                    self._spinner.update(message)
            else:
                self._spinner.start(message)
            return

        self._spinner.stop()


__all__ = ["ActivityIndicator", "State"]
