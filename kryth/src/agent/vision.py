"""Vision layer — fallback element detection using LLM vision.

Only used when DOM-based selectors fail (after retries). Takes a
screenshot, sends it to an LLM with vision capabilities, gets back
coordinates, and performs the action at those coordinates.

Trigger conditions:
- Element not found after N retries
- Canvas-based UI (no DOM elements)
- Verification needed for visual state
- Generic "click at X,Y" or "find element visually" requests
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from agent.browser.selectors import SelectorResult

logger = logging.getLogger(__name__)


@dataclass
class VisionResult:
    """Result from a vision-based element detection."""

    element: object | None = None  # Playwright element handle if found
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    label: str = ""
    confidence: float = 0.0
    error: str = ""


class VisionEngine:
    """Fallback vision engine that uses LLM vision to find elements.

    Takes screenshots, queries an LLM for coordinates, and translates
    those coordinates into Playwright actions.

    The engine is a last-resort fallback in the selector recovery chain.
    It should only be invoked when all DOM-based strategies have failed.

    Usage:
        engine = VisionEngine()
        result = engine.find_element(page, "the submit button", action="click")
        if result.element:
            result.element.click()
    """

    def __init__(
        self,
        model: str = "",
        api_key: str = "",
        base_url: str = "",
    ) -> None:
        self._model = model or os.environ.get(
            "KRYTH_VISION_MODEL",
            os.environ.get("KRYTH_MAIN_MODEL", "gpt-4o-mini"),
        )
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url or os.environ.get("KRYTH_BASE_URL", "")

    def find_element(
        self,
        page,
        selector: str,
        action: str = "click",
    ) -> VisionResult:
        """Find an element using vision.

        Takes a screenshot, asks the LLM to describe where the element is,
        then attempts to locate and interact with it.

        Args:
            page: Playwright Page object.
            selector: Text description of the element to find (e.g., "Submit button").
            action: The intended action (click, type, hover, etc.).

        Returns:
            VisionResult with element handle or coordinates.
        """
        # Take screenshot
        screenshot_path = self._take_screenshot(page)
        if not screenshot_path:
            return VisionResult(error="Failed to take screenshot")

        # Query the vision model
        try:
            coords = self._query_vision_model(
                screenshot_path,
                selector,
                action,
            )
        except Exception as e:
            return VisionResult(
                error=f"Vision model query failed: {e}",
            )

        if not coords:
            return VisionResult(
                error="Vision model could not locate the element",
            )

        # Try to find element at the returned coordinates
        try:
            element = self._element_at(page, coords["x"], coords["y"])
            if element:
                return VisionResult(
                    element=element,
                    x=coords["x"],
                    y=coords["y"],
                    width=coords.get("width", 0),
                    height=coords.get("height", 0),
                    label=coords.get("label", selector),
                    confidence=coords.get("confidence", 0.5),
                )
        except Exception as e:
            logger.warning("Failed to locate element at coordinates: %s", e)

        # Return coordinates even without element handle
        return VisionResult(
            x=coords["x"],
            y=coords["y"],
            label=coords.get("label", selector),
            confidence=coords.get("confidence", 0.3),
            error="Element handle not found at coordinates",
        )

    def verify_page(
        self,
        page,
        expected_content: str,
    ) -> bool:
        """Verify that the page visually contains expected content.

        Takes a screenshot and asks the vision model to confirm.
        """
        screenshot_path = self._take_screenshot(page)
        if not screenshot_path:
            return False

        try:
            result = self._query_verification(screenshot_path, expected_content)
            return result
        except Exception as e:
            logger.warning("Vision verification failed: %s", e)
            return False

    def get_page_summary(self, page) -> str:
        """Get a visual summary of the current page.

        Useful for understanding layout when DOM is complex/obscured.
        """
        screenshot_path = self._take_screenshot(page)
        if not screenshot_path:
            return ""

        prompt = (
            "Describe this webpage in detail. What is the main purpose? "
            "What are the key UI elements visible (buttons, forms, links, headings)? "
            "List them with their approximate position and what they do."
        )

        return self._query_summary(screenshot_path, prompt)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _take_screenshot(self, page) -> str | None:
        """Take a screenshot and return the file path."""
        try:
            tmp = Path(tempfile.mkdtemp()) / "vision_screenshot.png"
            page.screenshot(path=str(tmp), full_page=False)
            return str(tmp)
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            return None

    def _query_vision_model(
        self,
        screenshot_path: str,
        selector: str,
        action: str,
    ) -> dict | None:
        """Ask a vision-capable LLM to locate an element in a screenshot.

        Returns:
            Dict with x, y, width, height, label, confidence keys, or None.
        """
        try:
            from openai import OpenAI
        except ImportError:
            logger.error("OpenAI package not available for vision queries")
            return None

        with open(screenshot_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        system_prompt = (
            "You are a vision-based element locator. Given a screenshot of a webpage "
            "and a description of an element to find, return the element's bounding box "
            "as a JSON object with keys: x, y, width, height, label, confidence.\n"
            "- x, y are the center coordinates of the element in pixels\n"
            "- width and height are the element's dimensions\n"
            "- label is a short description of what you found\n"
            "- confidence is your confidence (0.0-1.0) that this is the right element\n\n"
            "Return ONLY valid JSON, no other text."
        )

        user_prompt = (
            f"Find this element on the page: '{selector}'\n"
            f"Intended action: {action}\n"
            "Return the center coordinates and bounding box."
        )

        try:
            client = OpenAI(
                api_key=self._api_key or None,
                base_url=self._base_url or None,
            )
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                temperature=0,
                max_tokens=300,
            )

            text = (response.choices[0].message.content or "").strip()
            # Extract JSON
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(text[start:end + 1])
                if isinstance(data, dict) and "x" in data and "y" in data:
                    return data
        except Exception as e:
            logger.warning("Vision model query failed: %s", e)

        return None

    def _query_verification(self, screenshot_path: str, expected: str) -> bool:
        """Ask the vision model to verify content."""
        try:
            from openai import OpenAI
        except ImportError:
            return False

        with open(screenshot_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        try:
            client = OpenAI(
                api_key=self._api_key or None,
                base_url=self._base_url or None,
            )
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Does this page contain '{expected}'? Reply with YES or NO only.",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
                temperature=0,
                max_tokens=10,
            )
            text = (response.choices[0].message.content or "").strip().upper()
            return text.startswith("YES")
        except Exception as e:
            logger.warning("Vision verification failed: %s", e)
            return False

    def _query_summary(self, screenshot_path: str, prompt: str) -> str:
        """Get a textual summary of a screenshot from the vision model."""
        try:
            from openai import OpenAI
        except ImportError:
            return ""

        with open(screenshot_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")

        try:
            client = OpenAI(
                api_key=self._api_key or None,
                base_url=self._base_url or None,
            )
            response = client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                temperature=0,
                max_tokens=500,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning("Vision summary failed: %s", e)
            return ""

    def _element_at(self, page, x: float, y: float):
        """Get the topmost element at the given page coordinates."""
        try:
            return page.evaluate_handle(
                f"document.elementFromPoint({x}, {y})"
            )
        except Exception:
            return None

    def click_at(self, page, x: float, y: float) -> str:
        """Click at specific page coordinates."""
        try:
            page.mouse.click(x, y)
            return f"Clicked at ({x:.0f}, {y:.0f})"
        except Exception as e:
            return f"[ERROR] click_at failed: {e}"

    def type_at(self, page, x: float, y: float, text: str) -> str:
        """Click at coordinates and type text."""
        try:
            page.mouse.click(x, y)
            time.sleep(0.1)
            page.keyboard.type(text)
            return f"Typed at ({x:.0f}, {y:.0f})"
        except Exception as e:
            return f"[ERROR] type_at failed: {e}"
