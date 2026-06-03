"""Page controller — high-level browser interaction API.

Provides navigation, clicking, typing, hovering, scrolling, screenshots,
text extraction, and link extraction. Integrates the self-healing selector
engine and optional human-like behavior (random pauses, natural mouse
movement).
"""

from __future__ import annotations

import base64
import logging
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import ElementHandle, Page

from agent.browser._human import is_human_like
from agent.browser.selectors import SelectorEngine, SelectorResult

logger = logging.getLogger(__name__)


class PageController:
    """High-level page interaction controller.

    Wraps a Playwright Page with convenience methods for navigation,
    element interaction, content extraction, and screenshot capabilities.
    Integrates self-healing selectors and optional human-like behavior.

    Usage:
        controller = PageController(page)
        controller.navigate("https://example.com")
        controller.click("#submit")
        text = controller.extract_text("body")
    """

    def __init__(self, page: Page, vision_fallback: Optional[callable] = None) -> None:
        self._page = page
        self._selector_engine = SelectorEngine(page, vision_fallback=vision_fallback)
        self._vision_fallback = vision_fallback
        self._human_like = is_human_like()

    @property
    def page(self) -> Page:
        return self._page

    @page.setter
    def page(self, new_page: Page) -> None:
        self._page = new_page
        self._selector_engine = SelectorEngine(
            new_page, vision_fallback=self._vision_fallback
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(
        self,
        url: str,
        *,
        timeout: float = 30000,
        wait_until: str = "domcontentloaded",
    ) -> str:
        """Navigate to a URL and wait for the page to load.

        Returns a status message.
        """
        try:
            self._page.goto(url, wait_until=wait_until, timeout=timeout)
            self._human_pause()
            try:
                self._page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            return f"navigated to {url}"
        except Exception as e:
            return f"[ERROR] navigate failed: {e}"

    def back(self) -> str:
        """Navigate back in history."""
        try:
            self._page.go_back()
            self._human_pause()
            return "navigated back"
        except Exception as e:
            return f"[ERROR] back failed: {e}"

    def forward(self) -> str:
        """Navigate forward in history."""
        try:
            self._page.go_forward()
            self._human_pause()
            return "navigated forward"
        except Exception as e:
            return f"[ERROR] forward failed: {e}"

    def reload(self) -> str:
        """Reload the current page."""
        try:
            self._page.reload()
            self._human_pause()
            return "page reloaded"
        except Exception as e:
            return f"[ERROR] reload failed: {e}"

    # ------------------------------------------------------------------
    # Element interaction
    # ------------------------------------------------------------------

    def click(
        self,
        selector: str,
        *,
        timeout: float = 5000,
        retries: int = 3,
    ) -> str:
        """Click an element identified by the self-healing selector.

        Uses the full recovery chain: CSS -> text -> role -> XPath -> vision.
        """
        result = self._selector_engine.find_element(
            selector, timeout=timeout, retries=retries,
        )
        if not result.element:
            return f"[ERROR] click failed: {result.error}"

        try:
            if self._human_like:
                self._human_mouse_move(result.element)
                self._human_pause()
                result.element.hover()
                self._human_pause(50, 150)

            result.element.click()
            self._human_pause()
            return f"clicked '{selector}' (via {result.strategy})"
        except Exception as e:
            return f"[ERROR] click failed: {e}"

    def type_text(
        self,
        selector: str,
        text: str,
        *,
        delay: int = 0,
        timeout: float = 5000,
        retries: int = 3,
    ) -> str:
        """Type text into an element (character by character).

        Uses the self-healing selector chain.
        """
        result = self._selector_engine.find_element(
            selector, timeout=timeout, retries=retries,
        )
        if not result.element:
            return f"[ERROR] type_text failed: {result.error}"

        try:
            result.element.click()
            if self._human_like:
                result.element.fill("")  # Clear first
                # Type with realistic delays between characters
                for char in text:
                    result.element.type(char, delay=random.randint(30, 120))
            else:
                result.element.type(text, delay=delay)
            self._human_pause()
            return f"typed into '{selector}' (via {result.strategy})"
        except Exception as e:
            return f"[ERROR] type_text failed: {e}"

    def fill(
        self,
        selector: str,
        value: str,
        *,
        timeout: float = 5000,
        retries: int = 3,
    ) -> str:
        """Fill a form field (fast, single operation).

        Uses ``fill()`` which clears and fills in one step.
        """
        result = self._selector_engine.find_element(
            selector, timeout=timeout, retries=retries,
        )
        if not result.element:
            return f"[ERROR] fill failed: {result.error}"

        try:
            result.element.click()
            self._human_pause(50, 150)
            result.element.fill(value)
            self._human_pause()
            return f"filled '{selector}' (via {result.strategy})"
        except Exception as e:
            return f"[ERROR] fill failed: {e}"

    def select_option(
        self,
        selector: str,
        value: str,
        *,
        timeout: float = 5000,
    ) -> str:
        """Select an option from a <select> element."""
        result = self._selector_engine.find_element(selector, timeout=timeout)
        if not result.element:
            return f"[ERROR] select failed: {result.error}"

        try:
            result.element.select_option(value)
            self._human_pause()
            return f"selected '{value}' in '{selector}'"
        except Exception as e:
            return f"[ERROR] select failed: {e}"

    def hover(
        self,
        selector: str,
        *,
        timeout: float = 5000,
    ) -> str:
        """Hover over an element."""
        result = self._selector_engine.find_element(selector, timeout=timeout)
        if not result.element:
            return f"[ERROR] hover failed: {result.error}"

        try:
            if self._human_like:
                self._human_mouse_move(result.element)
                self._human_pause(50, 150)
            result.element.hover()
            self._human_pause()
            return f"hovered '{selector}'"
        except Exception as e:
            return f"[ERROR] hover failed: {e}"

    def press_key(self, key: str) -> str:
        """Press a keyboard key."""
        try:
            self._page.keyboard.press(key)
            self._human_pause()
            return f"pressed '{key}'"
        except Exception as e:
            return f"[ERROR] press_key failed: {e}"

    def scroll(
        self,
        direction: str = "down",
        amount: int = 800,
    ) -> str:
        """Scroll the page in a direction."""
        try:
            dx, dy = 0, 0
            if direction == "down":
                dy = amount
            elif direction == "up":
                dy = -amount
            elif direction == "right":
                dx = amount
            elif direction == "left":
                dx = -amount
            elif direction == "to_top":
                dy = -self._page.evaluate("window.scrollY")
            elif direction == "to_bottom":
                dy = self._page.evaluate(
                    "document.body.scrollHeight - window.innerHeight - window.scrollY"
                )

            self._page.evaluate(f"window.scrollBy({dx}, {dy})")
            self._human_pause()
            return f"scrolled {direction}"
        except Exception as e:
            return f"[ERROR] scroll failed: {e}"

    def evaluate(self, expression: str) -> str:
        """Run JavaScript in the page context."""
        try:
            result = self._page.evaluate(expression)
            return str(result)
        except Exception as e:
            return f"[ERROR] evaluate failed: {e}"

    # ------------------------------------------------------------------
    # Content extraction
    # ------------------------------------------------------------------

    def extract_text(self, selector: str = "body") -> str:
        """Extract visible text from elements matching a selector."""
        try:
            if selector in ("", "body", "*"):
                text = self._page.inner_text("body") if self._page.query_selector("body") else ""
                return text[:30000]
            elements = self._page.query_selector_all(selector)
            if not elements:
                return ""
            texts = [el.inner_text().strip() for el in elements if el]
            return "\n".join(t for t in texts if t)[:30000]
        except Exception as e:
            return f"[ERROR] extract_text failed: {e}"

    def extract_html(self) -> str:
        """Get the full page HTML."""
        try:
            return self._page.content()
        except Exception as e:
            return f"[ERROR] extract_html failed: {e}"

    def extract_links(self) -> list[dict]:
        """Extract all links from the page."""
        try:
            links = self._page.query_selector_all("a[href]")
            results = []
            for link in links:
                href = link.get_attribute("href")
                text = (link.inner_text() or "").strip()[:100]
                if href and href.strip():
                    results.append({"href": href.strip(), "text": text})
            return results[:200]
        except Exception as e:
            return [{"error": str(e)}]

    def extract_forms(self) -> list[dict]:
        """Extract all form elements and their details."""
        try:
            forms = self._page.query_selector_all("form")
            results = []
            for form in forms:
                inputs = form.query_selector_all(
                    "input, select, textarea, button"
                )
                fields = []
                for inp in inputs:
                    tag = inp.evaluate("el => el.tagName.toLowerCase()")
                    name = inp.get_attribute("name") or ""
                    _id = inp.get_attribute("id") or ""
                    _type = inp.get_attribute("type") or tag
                    placeholder = inp.get_attribute("placeholder") or ""
                    label_text = ""
                    if _id:
                        label = self._page.query_selector(f"label[for='{_id}']")
                        if label:
                            label_text = label.inner_text().strip()
                    fields.append({
                        "tag": tag,
                        "name": name,
                        "id": _id,
                        "type": _type,
                        "placeholder": placeholder,
                        "label": label_text,
                    })
                results.append({"fields": fields})
            return results
        except Exception as e:
            return [{"error": str(e)}]

    def url(self) -> str:
        """Get the current page URL."""
        try:
            return self._page.url
        except Exception as e:
            return f"[ERROR] get_url failed: {e}"

    def title(self) -> str:
        """Get the current page title."""
        try:
            return self._page.title()
        except Exception as e:
            return f"[ERROR] get_title failed: {e}"

    # ------------------------------------------------------------------
    # Screenshot
    # ------------------------------------------------------------------

    def screenshot(
        self,
        *,
        full_page: bool = False,
        path: str = "",
        encoding: str = "base64",
    ) -> str:
        """Take a screenshot.

        Args:
            full_page: Capture the full scrollable page.
            path: File path to save to. If empty, saves to a temp file.
            encoding: "base64" returns base64 string, "binary" returns path.

        Returns:
            Base64-encoded string or file path, depending on encoding.
        """
        try:
            if not path:
                tmp = Path(tempfile.mkdtemp()) / "screenshot.png"
                path = str(tmp)

            self._page.screenshot(path=path, full_page=full_page)

            if encoding == "base64":
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode("utf-8")
            return path
        except Exception as e:
            return f"[ERROR] screenshot failed: {e}"

    def element_screenshot(self, selector: str) -> str:
        """Take a screenshot of a specific element.

        Returns a base64-encoded string.
        """
        result = self._selector_engine.find_element(selector)
        if not result.element:
            return f"[ERROR] element not found: {selector}"

        try:
            tmp = Path(tempfile.mkdtemp()) / "element.png"
            result.element.screenshot(path=str(tmp))
            with open(tmp, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            return f"[ERROR] element_screenshot failed: {e}"

    def get_state(self) -> dict:
        """Get full browser state as a dict."""
        try:
            return {
                "url": self._page.url,
                "title": self._page.title(),
                "viewport": self._page.viewport_size,
            }
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Human-like behavior helpers
    # ------------------------------------------------------------------

    def _human_pause(self, min_ms: int = 100, max_ms: int = 500) -> None:
        """Sleep for a random duration to simulate human reaction time."""
        if self._human_like:
            time.sleep(random.randint(min_ms, max_ms) / 1000.0)

    def _human_mouse_move(self, element: ElementHandle) -> None:
        """Move the mouse naturally toward an element."""
        try:
            box = element.bounding_box()
            if box:
                target_x = box["x"] + box["width"] / 2
                target_y = box["y"] + box["height"] / 2

                # Move in stages for a natural feel
                current_x, current_y = 100, 100  # approximate starting position
                steps = random.randint(3, 6)
                for i in range(1, steps + 1):
                    t = i / steps
                    x = current_x + (target_x - current_x) * t + random.randint(-5, 5)
                    y = current_y + (target_y - current_y) * t + random.randint(-5, 5)
                    self._page.mouse.move(x, y)
                    time.sleep(random.uniform(0.02, 0.08))
        except Exception:
            pass  # Graceful degradation
