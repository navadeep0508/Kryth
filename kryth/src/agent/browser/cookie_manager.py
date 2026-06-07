"""Cookie persistence for browser profiles.

Cookies are read from / written to the Playwright ``storage_state`` JSON format
so they round-trip cleanly through ``BrowserContext.storage_state()`` and
``BrowserProfile(storage_state=...)``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

COOKIES_FILE = "cookies.json"


class CookieManager:
    """Persist and restore browser cookies for a single profile.

    Parameters
    ----------
    profile_dir:
        Root directory of the profile.
    """

    def __init__(self, profile_dir: Path) -> None:
        self._path = profile_dir / COOKIES_FILE

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, cookies: list[dict[str, Any]]) -> None:
        """Write *cookies* list to disk (Playwright format)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        logger.debug("Saved %d cookies → %s", len(cookies), self._path)

    def load(self) -> list[dict[str, Any]]:
        """Return cookies from disk, or an empty list if none saved."""
        if not self._path.exists():
            return []
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load cookies: %s", exc)
            return []

    def clear(self) -> None:
        """Delete the saved cookies file."""
        if self._path.exists():
            self._path.unlink()

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------

    def cookies_for_domain(self, domain: str) -> list[dict[str, Any]]:
        """Return cookies that apply to *domain*."""
        domain = domain.lstrip(".")
        return [
            c for c in self.load()
            if domain in c.get("domain", "").lstrip(".")
        ]

    def remove_expired(self) -> int:
        """Remove cookies whose ``expires`` timestamp is in the past.

        Returns the number of cookies removed.
        """
        now = time.time()
        cookies = self.load()
        before = len(cookies)
        cookies = [
            c for c in cookies
            if c.get("expires", -1) < 0 or c.get("expires", now + 1) > now
        ]
        removed = before - len(cookies)
        if removed:
            self.save(cookies)
        return removed

    def has_session_cookie(self, domain: str) -> bool:
        """Return True if at least one non-expired session cookie exists for *domain*."""
        now = time.time()
        for c in self.cookies_for_domain(domain):
            exp = c.get("expires", -1)
            if exp < 0 or exp > now:
                return True
        return False

    # ------------------------------------------------------------------
    # storage_state helpers (Playwright round-trip)
    # ------------------------------------------------------------------

    def to_storage_state(
        self,
        local_storage: dict[str, list[dict]] | None = None,
    ) -> dict[str, Any]:
        """Build a Playwright-compatible storage_state dict."""
        return {
            "cookies": self.load(),
            "origins": local_storage or [],
        }

    def import_storage_state(self, state: dict[str, Any]) -> None:
        """Import cookies from a Playwright storage_state dict."""
        cookies = state.get("cookies", [])
        if cookies:
            self.save(cookies)
