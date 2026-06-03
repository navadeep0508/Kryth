"""Browser automation layer for KRYTH v2.

Provides persistent Playwright-based browser management with self-healing
selectors, profile persistence, and human-like interaction behaviors.

Usage:
    from agent.browser import get_browser, get_page

    page = get_page()
    page.goto("https://example.com")
    text = page_controller.extract_text("body")
"""

from __future__ import annotations

from agent.browser._human import is_human_like
from agent.browser.browser_manager import BrowserManager, get_browser, get_page, cleanup
from agent.browser.page_controller import PageController
from agent.browser.selectors import SelectorEngine, SelectorResult
from agent.browser.profiles import ProfileManager
from agent.browser.downloads import DownloadHandler
from agent.browser.uploads import UploadHandler

__all__ = [
    "BrowserManager",
    "PageController",
    "SelectorEngine",
    "SelectorResult",
    "ProfileManager",
    "DownloadHandler",
    "UploadHandler",
    "get_browser",
    "get_page",
    "cleanup",
    "is_human_like",
]
