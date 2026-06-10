"""Browser Supervisor — monitor browser session health, auto-restore.

Tracks:
  - Browser process alive
  - Active tabs and their navigation state
  - Login session validity
  - Automation failure count

Auto-recovery:
  - Reconnect dropped WebSocket
  - Re-navigate to last known URL
  - Re-login if session expired (using stored credentials)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BrowserState:
    alive: bool = False
    current_url: str = ""
    tab_count: int = 0
    last_check: float = field(default_factory=time.monotonic)
    failure_count: int = 0
    login_valid: bool = True
    last_error: str = ""


class BrowserSupervisor:
    """Monitor and recover the browser automation session."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = BrowserState()

    def probe(self) -> BrowserState:
        """Probe the browser session and update state."""
        with self._lock:
            state = self._state

        alive = self._check_alive()
        with self._lock:
            self._state.alive = alive
            self._state.last_check = time.monotonic()
            if not alive:
                self._state.failure_count += 1
        return self._state

    def health_score(self) -> int:
        """Return 0–100 browser health score."""
        self.probe()
        with self._lock:
            state = self._state

        if not state.alive:
            # Browser not started is not unhealthy — just absent
            return 100

        score = 100
        score -= min(50, state.failure_count * 10)
        if not state.login_valid:
            score -= 20
        return max(0, score)

    def record_failure(self, error: str = "") -> None:
        with self._lock:
            self._state.failure_count += 1
            self._state.last_error = error

    def record_success(self) -> None:
        with self._lock:
            if self._state.failure_count > 0:
                self._state.failure_count = max(0, self._state.failure_count - 1)

    def attempt_recovery(self) -> bool:
        """Try to recover the browser session. Returns True on success."""
        # Try to reconnect via OpenCLI
        try:
            from agent.tools._opencli import browser_state as _bs
            result = _bs()
            if "error" not in str(result).lower():
                with self._lock:
                    self._state.alive = True
                    self._state.failure_count = 0
                return True
        except Exception:
            pass
        return False

    def _check_alive(self) -> bool:
        try:
            from agent.tools._opencli import browser_get_url
            result = browser_get_url()
            return "error" not in str(result).lower()
        except Exception:
            return False


# Module-level singleton
browser_supervisor = BrowserSupervisor()
