"""Playwright-based browser automation — fast, reliable, no external deps.

Replaces OpenCLI for browser automation. Uses Playwright's native Python
API instead of wrapping CLI commands. Supports both headless and headed
browser modes.

Install: pip install playwright && playwright install chromium
"""

from __future__ import annotations

import base64
import os
import tempfile
import time
from pathlib import Path
from typing import Optional


def _err(msg: str) -> str:
    return f"[ERROR] {msg}"


def _ok(msg: str) -> str:
    return msg


def _playwright_available() -> bool:
    try:
        import playwright.sync_api
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Global Playwright browser instance (lazy, shared across calls)
# ---------------------------------------------------------------------------

_playwright = None
_browser = None
_page = None
_page_id = 0


def _ensure_page() -> tuple:
    """Start Playwright and return (browser, page)."""
    global _playwright, _browser, _page, _page_id
    if _page is not None:
        try:
            # Check if page is still alive
            _page.title()
            return _browser, _page
        except Exception:
            _page = None

    from playwright.sync_api import sync_playwright

    if _playwright is None:
        _playwright = sync_playwright().start()

    if _browser is None or not _browser.is_connected():
        try:
            _browser = _playwright.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except Exception:
            # Fall back to headless if headed fails
            _browser = _playwright.chromium.launch(headless=True)

    context = _browser.contexts[0] if _browser.contexts else _browser.new_context(
        viewport={"width": 1280, "height": 720},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    )
    _page = context.new_page()
    _page_id += 1
    return _browser, _page


def _get_page():
    """Get or create a page. Creates a new one if the current is closed."""
    global _page
    _, page = _ensure_page()
    return page


def _cleanup():
    """Clean up browser resources. Called at the end of the session."""
    global _playwright, _browser, _page
    try:
        if _page is not None:
            _page.close()
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
    _page = None


def ensure_available() -> str | None:
    """Return an error string if Playwright is not installed, else None."""
    if not _playwright_available():
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
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # Wait for page to settle
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        return _ok(f"navigated to {url}")
    except Exception as e:
        return _err(f"open failed: {e}")


def get_url() -> str:
    """Get the current page URL."""
    err = ensure_available()
    if err:
        return err
    try:
        return _get_page().url
    except Exception as e:
        return _err(f"get_url failed: {e}")


def get_title() -> str:
    """Get the current page title."""
    err = ensure_available()
    if err:
        return err
    try:
        return _get_page().title()
    except Exception as e:
        return _err(f"get_title failed: {e}")


def get_html() -> str:
    """Get the full page HTML."""
    err = ensure_available()
    if err:
        return err
    try:
        return _get_page().content()
    except Exception as e:
        return _err(f"get_html failed: {e}")


def state() -> str:
    """Get full browser state."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        return (
            f"URL: {page.url}\n"
            f"Title: {page.title()}\n"
            f"Viewport: {page.viewport_size}\n"
        )
    except Exception as e:
        return _err(f"state failed: {e}")


def back() -> str:
    """Navigate back."""
    err = ensure_available()
    if err:
        return err
    try:
        _get_page().go_back()
        return _ok("navigated back")
    except Exception as e:
        return _err(f"back failed: {e}")


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------


def click(selector: str) -> str:
    """Click an element by CSS selector."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        el = page.wait_for_selector(selector, timeout=5000)
        if not el:
            return _err(f"CSS selector '{selector}' matched 0 elements")
        el.click()
        return _ok(f"clicked {selector}")
    except Exception as e:
        return _err(f"click failed: {e}")


def type_text(selector: str, text: str) -> str:
    """Type text into an element by CSS selector."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        el = page.wait_for_selector(selector, timeout=5000)
        if not el:
            return _err(f"CSS selector '{selector}' matched 0 elements")
        el.type(text)
        return _ok(f"typed into {selector}")
    except Exception as e:
        return _err(f"type failed: {e}")


def fill(selector: str, value: str) -> str:
    """Fill a form field by CSS selector."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        el = page.wait_for_selector(selector, timeout=5000)
        if not el:
            return _err(f"CSS selector '{selector}' matched 0 elements")
        el.fill(value)
        return _ok(f"filled {selector}")
    except Exception as e:
        return _err(f"fill failed: {e}")


def select(selector: str, value: str) -> str:
    """Select an option from a dropdown."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        el = page.wait_for_selector(selector, timeout=5000)
        if not el:
            return _err(f"CSS selector '{selector}' matched 0 elements")
        el.select_option(value)
        return _ok(f"selected {value} in {selector}")
    except Exception as e:
        return _err(f"select failed: {e}")


def keys(combo: str) -> str:
    """Send keyboard shortcut."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        page.keyboard.press(combo)
        return _ok(f"sent {combo}")
    except Exception as e:
        return _err(f"keys failed: {e}")


def scroll(direction: str = "down") -> str:
    """Scroll the page."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        dx, dy = 0, 0
        if direction == "down":
            dy = 800
        elif direction == "up":
            dy = -800
        elif direction == "right":
            dx = 800
        elif direction == "left":
            dx = -800
        page.evaluate(f"window.scrollBy({dx}, {dy})")
        return _ok(f"scrolled {direction}")
    except Exception as e:
        return _err(f"scroll failed: {e}")


def eval_js(expression: str) -> str:
    """Run JavaScript in the page."""
    err = ensure_available()
    if err:
        return err
    try:
        result = _get_page().evaluate(expression)
        return str(result)
    except Exception as e:
        return _err(f"eval_js failed: {e}")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def extract(selector: str = "") -> str:
    """Extract text from elements matching a CSS selector."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        if not selector or selector in ("body", "*"):
            # Return entire page text
            text = page.inner_text("body") if page.query_selector("body") else page.content()
            return text[:10000]  # cap at 10k chars
        elements = page.query_selector_all(selector)
        if not elements:
            return "[]"
        texts = [el.inner_text().strip() for el in elements if el]
        import json
        return json.dumps([t for t in texts if t])
    except Exception as e:
        return _err(f"extract failed: {e}")


def find(selector: str) -> str:
    """Find elements by CSS selector and return their details."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        elements = page.query_selector_all(selector)
        if not elements:
            return "[]"
        results = []
        for el in elements[:20]:
            tag = el.evaluate("el => el.tagName.toLowerCase()")
            text = el.inner_text().strip()[:80]
            attrs = el.evaluate("""el => {
                const a = {};
                for (const attr of el.attributes) a[attr.name] = attr.value;
                return a;
            }""")
            results.append({"tag": tag, "text": text, "attrs": attrs})
        import json
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        return _err(f"find failed: {e}")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------


def screenshot() -> str:
    """Take a screenshot. Returns the file path."""
    err = ensure_available()
    if err:
        return err
    try:
        tmp = Path(tempfile.mkdtemp()) / "screenshot.png"
        _get_page().screenshot(path=str(tmp), full_page=False)
        return str(tmp)
    except Exception as e:
        return _err(f"screenshot failed: {e}")


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------


def tab_list() -> str:
    """List all open pages/tabs."""
    err = ensure_available()
    if err:
        return err
    try:
        global _playwright, _browser
        _, _ = _ensure_page()
        if _browser is None:
            return "[]"
        contexts = _browser.contexts
        if not contexts:
            return "[]"
        results = []
        for ctx in contexts:
            for i, p in enumerate(ctx.pages):
                try:
                    results.append(f"[{i}] {p.title()} — {p.url}")
                except Exception:
                    results.append(f"[{i}] (closed)")
        return "\n".join(results) if results else "[]"
    except Exception as e:
        return _err(f"tab_list failed: {e}")


def tab_new(url: str = "") -> str:
    """Open a new tab. Optionally navigate to a URL."""
    global _page
    err = ensure_available()
    if err:
        return err
    try:
        _, page = _ensure_page()
        ctx = _browser.contexts[0] if _browser.contexts else None
        if ctx is None:
            return _err("no browser context available")
        new_page = ctx.new_page()
        _page = new_page
        if url:
            new_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return _ok(f"opened new tab with {url}")
        return _ok("opened new tab")
    except Exception as e:
        return _err(f"tab_new failed: {e}")


def tab_select(target_id: str) -> str:
    """Switch to a specific tab by its index (as shown in tab_list)."""
    global _page
    err = ensure_available()
    if err:
        return err
    try:
        _, _ = _ensure_page()
        if _browser is None:
            return _err("no browser available")
        contexts = _browser.contexts
        if not contexts:
            return _err("no browser contexts")
        # Try to match by index
        if target_id.isdigit():
            idx = int(target_id)
            all_pages = []
            for ctx in contexts:
                all_pages.extend(ctx.pages)
            if 0 <= idx < len(all_pages):
                _page = all_pages[idx]
                _page.bring_to_front()
                return _ok(f"switched to tab [{idx}]")
        # Try to match by URL or title substring
        for ctx in contexts:
            for p in ctx.pages:
                try:
                    if target_id in p.url or target_id in p.title():
                        _page = p
                        _page.bring_to_front()
                        return _ok(f"switched to tab matching '{target_id}'")
                except Exception:
                    continue
        return _err(f"tab '{target_id}' not found")
    except Exception as e:
        return _err(f"tab_select failed: {e}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


def download(url: str, output_dir: str = ".") -> str:
    """Download content from a URL into a local directory."""
    err = ensure_available()
    if err:
        return err
    try:
        page = _get_page()
        os.makedirs(output_dir, exist_ok=True)
        with page.expect_download(timeout=30000) as download_info:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        dl = download_info.value
        suggested = dl.suggested_filename
        dest = os.path.join(output_dir, suggested)
        dl.save_as(dest)
        return _ok(f"downloaded to {dest}")
    except Exception as e:
        return _err(f"download failed: {e}")
