"""Persistent Playwright browser manager with profile support.

Manages a singleton Playwright instance with support for persistent
browser contexts (profiles) so sites stay logged in across sessions.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from agent.browser.profiles import ProfileManager

logger = logging.getLogger(__name__)

# Global state (lazy initialized)
_playwright: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None
_page_id: int = 0


class BrowserManager:
    """Singleton browser manager with persistent profile support.

    Usage:
        manager = BrowserManager()
        manager.start()
        page = manager.get_page()
        page.goto("https://example.com")
        manager.cleanup()
    """

    def __init__(
        self,
        headless: bool = False,
        profile_name: str = "",
        viewport: dict | None = None,
        user_agent: str = "",
    ) -> None:
        self._headless = headless
        self._profile_name = profile_name
        self._viewport = viewport or {"width": 1280, "height": 720}
        self._user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
        self._profile_manager = ProfileManager()
        self._proxy_settings: dict | None = None

    @staticmethod
    def is_available() -> bool:
        """Check if Playwright is installed."""
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except ImportError:
            return False

    def set_proxy(self, proxy: dict) -> None:
        """Set proxy configuration for the browser.

        Args:
            proxy: dict with 'server' key, optional 'username' and 'password'.
        """
        self._proxy_settings = proxy

    def start(self) -> None:
        """Start Playwright and launch the browser (or connect to existing)."""
        global _playwright, _browser, _context, _page

        if _browser is not None:
            try:
                if _browser.is_connected():
                    return  # Already running
            except Exception:
                pass

        _playwright = sync_playwright().start()

        launch_args = {
            "headless": self._headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        }

        if self._proxy_settings:
            launch_args["proxy"] = self._proxy_settings

        _browser = _playwright.chromium.launch(**launch_args)

        if self._profile_name and self._profile_manager.profile_exists(self._profile_name):
            profile_path = self._profile_manager.profile_path(self._profile_name)
            _context = _browser.new_context(
                viewport=self._viewport,
                user_agent=self._user_agent,
                storage_state=os.path.join(profile_path, "state.json")
                if os.path.exists(os.path.join(profile_path, "state.json"))
                else None,
            )
        else:
            _context = _browser.new_context(
                viewport=self._viewport,
                user_agent=self._user_agent,
            )

        _page = _context.new_page()
        logger.info("Browser started (headless=%s, profile=%s)", self._headless, self._profile_name)

    def get_page(self) -> Page:
        """Get or create the current page."""
        global _page, _page_id
        self.start()

        if _page is not None:
            try:
                # Check if the page is still alive
                _page.title()
                return _page
            except Exception:
                pass
            _page = None

        if _context is None:
            _browser = _playwright.chromium.launch(
                headless=self._headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            _context = _browser.new_context(
                viewport=self._viewport,
                user_agent=self._user_agent,
            )

        _page = _context.new_page()
        _page_id += 1
        return _page

    def get_context(self) -> BrowserContext:
        """Get the current browser context."""
        self.start()
        return _context

    def save_profile_state(self, name: str = "") -> str | None:
        """Save the current browser context's storage state (cookies, localStorage).

        Args:
            name: Profile name to save under. Uses current profile if empty.

        Returns:
            Path to the saved state file, or None on failure.
        """
        if _context is None:
            return None

        target = name or self._profile_name or "default"
        profile_dir = self._profile_manager.profile_path(target)
        state_path = os.path.join(profile_dir, "state.json")

        try:
            state = _context.storage_state(path=state_path)
            logger.info("Saved profile state to %s", state_path)
            return state_path
        except Exception as e:
            logger.warning("Failed to save profile state: %s", e)
            return None

    def new_tab(self, url: str = "") -> Page:
        """Open a new tab and return its page object."""
        global _page
        self.start()
        new_page = _context.new_page()
        _page = new_page
        if url:
            new_page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return new_page

    def list_tabs(self) -> list[dict]:
        """List all open tabs with their index, title, and URL."""
        self.start()
        tabs = []
        for ctx in _browser.contexts:
            for i, p in enumerate(ctx.pages):
                try:
                    tabs.append({
                        "index": i,
                        "title": p.title(),
                        "url": p.url,
                    })
                except Exception:
                    tabs.append({"index": i, "title": "(closed)", "url": ""})
        return tabs

    def switch_tab(self, target: str | int) -> bool:
        """Switch to a specific tab by index or by URL/title substring."""
        global _page
        self.start()
        all_pages = []
        for ctx in _browser.contexts:
            all_pages.extend(ctx.pages)

        if isinstance(target, int):
            if 0 <= target < len(all_pages):
                _page = all_pages[target]
                _page.bring_to_front()
                return True
        elif isinstance(target, str):
            if target.isdigit():
                idx = int(target)
                if 0 <= idx < len(all_pages):
                    _page = all_pages[idx]
                    _page.bring_to_front()
                    return True
            for p in all_pages:
                try:
                    if target in p.url or target in p.title():
                        _page = p
                        _page.bring_to_front()
                        return True
                except Exception:
                    continue
        return False

    def close_tab(self, index: int = -1) -> None:
        """Close a tab. Defaults to current tab if index is -1."""
        global _page
        self.start()
        all_pages = []
        for ctx in _browser.contexts:
            all_pages.extend(ctx.pages)

        if index == -1 and _page:
            _page.close()
            # Switch to the last available tab
            remaining = [
                p for p in all_pages if p != _page
                and (p.url or "").strip()
            ]
            _page = remaining[-1] if remaining else (
                _context.new_page() if _context else None
            )
        elif 0 <= index < len(all_pages):
            all_pages[index].close()

    def cleanup(self) -> None:
        """Clean up all browser resources."""
        global _playwright, _browser, _context, _page

        if self._profile_name:
            self.save_profile_state(self._profile_name)

        try:
            if _page is not None:
                _page.close()
        except Exception:
            pass
        try:
            if _context is not None:
                _context.close()
        except Exception:
            pass
        try:
            if _browser is not None:
                _browser.close()
        except Exception:
            pass
        try:
            if _playwright is not None:
                _playwright.stop()
        except Exception:
            pass

        _playwright = None
        _browser = None
        _context = None
        _page = None


# ---------------------------------------------------------------------------
# Module-level convenience functions (backward compatible with old API)
# ---------------------------------------------------------------------------

_manager_instance: BrowserManager | None = None


def get_browser(
    headless: bool = False,
    profile: str = "",
) -> BrowserManager:
    """Get or create the global browser manager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = BrowserManager(
            headless=headless,
            profile_name=profile,
        )
    return _manager_instance


def get_page() -> Page:
    """Get the current page from the global browser manager."""
    return get_browser().get_page()


def cleanup() -> None:
    """Clean up the global browser instance."""
    global _manager_instance
    if _manager_instance is not None:
        _manager_instance.cleanup()
        _manager_instance = None
