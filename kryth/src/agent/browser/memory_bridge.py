"""Bridge between BrowserProfileManager and KRYTH's MemoryManager.

Records browser session events (logins, navigation patterns, successful
automations) into the appropriate memory layers so the rest of the KRYTH
agent can learn from browser activity.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class BrowserMemoryBridge:
    """Connect browser profile events to MemoryManager layers.

    Parameters
    ----------
    memory_manager:
        An instance of ``agent.memory.memory_manager.MemoryManager``.
        Can be *None* — all methods become no-ops in that case.
    profile_name:
        The active browser profile name; used to tag stored entries.
    """

    def __init__(self, memory_manager: Any = None, profile_name: str = "default") -> None:
        self._mm = memory_manager
        self._profile = profile_name

    # ------------------------------------------------------------------
    # Session events
    # ------------------------------------------------------------------

    def record_login(self, url: str, success: bool = True) -> None:
        """Inform MemoryManager of a login event."""
        if self._mm is None:
            return
        try:
            from agent.memory.memory_manager import LAYER_EXECUTION
            self._mm.store(
                key=f"browser.login.{_netloc(url)}",
                value={
                    "url": url,
                    "profile": self._profile,
                    "success": success,
                    "time": time.time(),
                },
                scope=LAYER_EXECUTION,
            )
        except Exception as exc:
            logger.debug("BrowserMemoryBridge.record_login failed: %s", exc)

    def record_navigation(self, url: str, title: str = "") -> None:
        """Record a successful page navigation for experience learning."""
        if self._mm is None:
            return
        try:
            from agent.memory.memory_manager import LAYER_WORKING
            self._mm.store(
                key=f"browser.navigation.{_netloc(url)}",
                value={"url": url, "title": title, "time": time.time(), "profile": self._profile},
                scope=LAYER_WORKING,
            )
        except Exception as exc:
            logger.debug("BrowserMemoryBridge.record_navigation failed: %s", exc)

    def record_automation(self, task: str, url: str, success: bool, duration: float = 0.0) -> None:
        """Store the outcome of a browser automation task."""
        if self._mm is None:
            return
        try:
            from agent.memory.memory_manager import LAYER_WORKFLOW
            self._mm.store(
                key=f"browser.automation.{_netloc(url)}",
                value={
                    "task": task,
                    "url": url,
                    "profile": self._profile,
                    "success": success,
                    "duration": duration,
                    "time": time.time(),
                },
                scope=LAYER_WORKFLOW,
            )
        except Exception as exc:
            logger.debug("BrowserMemoryBridge.record_automation failed: %s", exc)

    def record_profile_switch(self, from_profile: str, to_profile: str) -> None:
        self._profile = to_profile
        if self._mm is None:
            return
        try:
            from agent.memory.memory_manager import LAYER_WORKING
            self._mm.store(
                key="browser.profile.active",
                value={"profile": to_profile, "previous": from_profile, "time": time.time()},
                scope=LAYER_WORKING,
            )
        except Exception as exc:
            logger.debug("BrowserMemoryBridge.record_profile_switch failed: %s", exc)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_recent_logins(self, domain: str) -> list[dict]:
        """Retrieve recent login records for *domain* from working memory."""
        if self._mm is None:
            return []
        try:
            val = self._mm.working.get(f"browser.login.{domain}")
            return [val] if val else []
        except Exception:
            return []


def _netloc(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url if "://" in url else f"https://{url}").netloc or url
    except Exception:
        return url
