"""Playwright-based browser automation — KRYTH v2 integration.

Delegates to the new agent.browser layer for all browser operations.
Provides backward-compatible API for existing tool calls while leveraging
the new self-healing selectors, persistent profiles, and human-like behavior.

Install: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from agent.browser._human import is_human_like

logger = logging.getLogger(__name__)


def _err(msg: str) -> str:
    return f"[ERROR] {msg}"


def _ok(msg: str) -> str:
    return msg


def _get_controller():
    """Lazy-import and return a PageController from the browser module."""
    from agent.browser.browser_manager import get_page
    from agent.browser.page_controller import PageController
    from agent.vision import VisionEngine
    page = get_page()
    vision = VisionEngine()
    return PageController(page, vision_fallback=vision.find_element)


def ensure_available() -> str | None:
    """Return an error string if Playwright is not installed, else None."""
    from agent.browser.browser_manager import BrowserManager
    if not BrowserManager.is_available():
        return (
            "Playwright is not installed. Run: pip install playwright && "
            "playwright install chromium"
        )
    return None


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def open_url(url: str) -> str:
    """Navigate to a URL."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.navigate(url)
    except Exception as e:
        return _err(f"open failed: {e}")


def get_url() -> str:
    """Get the current page URL."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.url()
    except Exception as e:
        return _err(f"get_url failed: {e}")


def get_title() -> str:
    """Get the current page title."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.title()
    except Exception as e:
        return _err(f"get_title failed: {e}")


def get_html() -> str:
    """Get the full page HTML."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.extract_html()
    except Exception as e:
        return _err(f"get_html failed: {e}")


def state() -> str:
    """Get full browser state."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        s = controller.get_state()
        return (
            f"URL: {s.get('url', '')}\n"
            f"Title: {s.get('title', '')}\n"
            f"Viewport: {s.get('viewport', {})}\n"
        )
    except Exception as e:
        return _err(f"state failed: {e}")


def back() -> str:
    """Navigate back."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.back()
    except Exception as e:
        return _err(f"back failed: {e}")


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------


def click(selector: str) -> str:
    """Click an element by selector (self-healing chain)."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.click(selector)
    except Exception as e:
        return _err(f"click failed: {e}")


def type_text(selector: str, text: str) -> str:
    """Type text into an element by selector."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.type_text(selector, text)
    except Exception as e:
        return _err(f"type failed: {e}")


def fill(selector: str, value: str) -> str:
    """Fill a form field by selector."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.fill(selector, value)
    except Exception as e:
        return _err(f"fill failed: {e}")


def select(selector: str, value: str) -> str:
    """Select an option from a dropdown."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.select_option(selector, value)
    except Exception as e:
        return _err(f"select failed: {e}")


def keys(combo: str) -> str:
    """Send keyboard shortcut."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.press_key(combo)
    except Exception as e:
        return _err(f"keys failed: {e}")


def scroll(direction: str = "down") -> str:
    """Scroll the page."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.scroll(direction)
    except Exception as e:
        return _err(f"scroll failed: {e}")


def eval_js(expression: str) -> str:
    """Run JavaScript in the page."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.evaluate(expression)
    except Exception as e:
        return _err(f"eval_js failed: {e}")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract(selector: str = "") -> str:
    """Extract text from elements matching a selector."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.extract_text(selector)
    except Exception as e:
        return _err(f"extract failed: {e}")


def find(selector: str) -> str:
    """Find elements by selector and return their details."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        text = controller.extract_text(selector)
        return text or "[]"
    except Exception as e:
        return _err(f"find failed: {e}")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def screenshot() -> str:
    """Take a screenshot. Returns the file path."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        controller = _get_controller()
        return controller.screenshot(encoding="binary")
    except Exception as e:
        return _err(f"screenshot failed: {e}")


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------


def tab_list() -> str:
    """List all open pages/tabs."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        from agent.browser.browser_manager import get_browser
        manager = get_browser()
        tabs = manager.list_tabs()
        if not tabs:
            return "[]"
        return "\n".join(
            f"[{t['index']}] {t['title']} — {t['url']}"
            for t in tabs
        )
    except Exception as e:
        return _err(f"tab_list failed: {e}")


def tab_new(url: str = "") -> str:
    """Open a new tab. Optionally navigate to a URL."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        from agent.browser.browser_manager import get_browser
        manager = get_browser()
        manager.new_tab(url)
        if url:
            return _ok(f"opened new tab with {url}")
        return _ok("opened new tab")
    except Exception as e:
        return _err(f"tab_new failed: {e}")


def tab_select(target_id: str) -> str:
    """Switch to a specific tab by its index or URL/title substring."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        from agent.browser.browser_manager import get_browser
        manager = get_browser()
        if target_id.isdigit():
            success = manager.switch_tab(int(target_id))
        else:
            success = manager.switch_tab(target_id)
        if success:
            return _ok(f"switched to tab matching '{target_id}'")
        return _err(f"tab '{target_id}' not found")
    except Exception as e:
        return _err(f"tab_select failed: {e}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download(url: str, output_dir: str = ".") -> str:
    """Download content from a URL into a local directory."""
    err_check = ensure_available()
    if err_check:
        return err_check
    try:
        from agent.browser.downloads import DownloadHandler
        from agent.browser.browser_manager import get_page
        page = get_page()
        handler = DownloadHandler(page, download_dir=output_dir)
        result = handler.download_url(url)
        return _ok(f"downloaded to {result}")
    except Exception as e:
        return _err(f"download failed: {e}")
