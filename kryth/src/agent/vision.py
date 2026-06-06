"""Vision layer — NVIDIA NIM vision API (stepfun-ai/step-3.7-flash).

Only used as LAST RESORT when DOM-based selectors fail. Takes a screenshot,
sends it to NVIDIA's vision model, gets back coordinates/description.

Requires: NVIDIA_API_KEY in config or environment.
Model: stepfun-ai/step-3.7-flash (via https://integrate.api.nvidia.com/v1)

Trigger conditions:
- Element not found after full selector recovery chain
- Canvas-based UI (no DOM elements)
- Visual verification needed
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

logger = logging.getLogger(__name__)


class SelectorResult:
    """Minimal compatibility shim for VisionResult / selector result types."""
    element: object = None
    strategy: str = ""
    confidence: float = 0.0
    error: str = ""

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_VISION_MODEL = "stepfun-ai/step-3.7-flash"


def _get_vision_model_and_key() -> tuple[str, str]:
    """Return (model_name, api_key) for the vision role.

    Uses model_config router when available; falls back to NVIDIA env vars.
    """
    try:
        from agent.model_config.router import get_llm
        client, model = get_llm("vision")
        # Extract api_key from client for raw HTTP call
        api_key = getattr(client, "api_key", "") or os.environ.get("NVIDIA_API_KEY", "")
        return model, api_key
    except Exception:
        return NVIDIA_VISION_MODEL, os.environ.get("NVIDIA_API_KEY", "")

IMAGE_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


@dataclass
class VisionResult:
    """Result from a vision-based element detection."""
    element: object | None = None
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    label: str = ""
    confidence: float = 0.0
    error: str = ""


def _get_nvidia_key() -> str:
    """Return NVIDIA_API_KEY from model_config, env, or legacy config."""
    # Try model_config vision role first
    try:
        _, key = _get_vision_model_and_key()
        if key and key not in ("not-configured", "not-set"):
            return key
    except Exception:
        pass
    # Direct env var
    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if key and key != "not-configured":
        return key
    # Legacy config file
    try:
        from kryth.config import _load
        cfg = _load()
        return cfg.get("nvidia_api_key", "")
    except Exception:
        return ""


def _image_to_data_url(path: str) -> str:
    """Convert an image file to a base64 data URL."""
    p = Path(path)
    mime = IMAGE_MIME_TYPES.get(p.suffix.lower(), "image/png")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _call_nvidia_vision(
    image_path: str,
    prompt: str,
    max_tokens: int = 1024,
) -> str:
    """Send a screenshot + prompt to NVIDIA vision API.

    Returns the model's text response. Raises on error.
    Always instructs the model to respond in English.
    """
    try:
        import requests
    except ImportError:
        raise RuntimeError("requests package required: pip install requests")

    api_key = _get_nvidia_key()
    if not api_key:
        raise RuntimeError(
            "NVIDIA_API_KEY not configured. Run /config to set it, "
            "or set NVIDIA_API_KEY in your .env file."
        )

    data_url = _image_to_data_url(image_path)

    # Force English output in the system message
    system_msg = (
        "You are a visual UI analyst. Always respond in English only. "
        "Never use any other language in your response."
    )

    payload = {
        "model": NVIDIA_VISION_MODEL,
        "messages": [
            {"role": "system", "content": system_msg},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                            "detail": "high",
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            },
        ],
        "max_tokens": max_tokens,
        "temperature": 0.1,
        "top_p": 0.95,
        "stream": False,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    response = requests.post(
        NVIDIA_API_URL,
        headers=headers,
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


class VisionEngine:
    """Fallback vision engine using NVIDIA's stepfun-ai/step-3.7-flash.

    Last resort in the selector recovery chain — only called when
    CSS, text, role, XPath, and all other DOM strategies have failed.

    Usage:
        engine = VisionEngine()
        result = engine.find_element(page, "the submit button")
        if result.x and result.y:
            page.mouse.click(result.x, result.y)
    """

    def find_element(
        self,
        page,
        selector: str,
        action: str = "click",
    ) -> VisionResult:
        """Find an element using NVIDIA vision.

        Takes a screenshot, asks the model where the element is,
        and returns coordinates.
        """
        screenshot_path = self._take_screenshot(page)
        if not screenshot_path:
            return VisionResult(error="Failed to take screenshot")

        prompt = (
            f"Look at this webpage screenshot. Find the element: '{selector}'\n"
            f"Intended action: {action}\n\n"
            "Return ONLY a JSON object with these keys:\n"
            '{"x": <center_x_pixels>, "y": <center_y_pixels>, '
            '"width": <width>, "height": <height>, '
            '"label": "<what you found>", "confidence": <0.0-1.0>}\n\n'
            "If the element is not visible, return: "
            '{"x": 0, "y": 0, "confidence": 0, "label": "not found", '
            '"width": 0, "height": 0}\n\n'
            "Respond in English only. Return ONLY the JSON, no other text."
        )

        try:
            text = _call_nvidia_vision(screenshot_path, prompt, max_tokens=300)
        except Exception as e:
            return VisionResult(error=f"Vision API failed: {e}")

        coords = self._parse_json(text)
        if not coords or coords.get("confidence", 0) < 0.1:
            return VisionResult(
                error=f"Vision could not locate: {selector}",
            )

        x, y = float(coords.get("x", 0)), float(coords.get("y", 0))
        if x == 0 and y == 0:
            return VisionResult(error=f"Element not found: {selector}")

        # Try to get element handle at coordinates
        element = None
        try:
            element = page.evaluate_handle(
                f"document.elementFromPoint({x}, {y})"
            )
        except Exception:
            pass

        return VisionResult(
            element=element,
            x=x,
            y=y,
            width=float(coords.get("width", 0)),
            height=float(coords.get("height", 0)),
            label=coords.get("label", selector),
            confidence=float(coords.get("confidence", 0.5)),
        )

    def verify_page(self, page, expected_content: str) -> bool:
        """Visually verify the page contains expected content."""
        screenshot_path = self._take_screenshot(page)
        if not screenshot_path:
            return False

        prompt = (
            f"Does this webpage screenshot contain: '{expected_content}'?\n"
            "Reply with exactly YES or NO in English. Nothing else."
        )

        try:
            text = _call_nvidia_vision(screenshot_path, prompt, max_tokens=10)
            return text.strip().upper().startswith("YES")
        except Exception as e:
            logger.warning("Vision verify failed: %s", e)
            return False

    def get_page_summary(self, page) -> str:
        """Get an English visual summary of the current page."""
        screenshot_path = self._take_screenshot(page)
        if not screenshot_path:
            return ""

        prompt = (
            "Describe this webpage in English. Include:\n"
            "1. Main purpose of the page\n"
            "2. Key UI elements (buttons, forms, inputs, links, headings)\n"
            "3. Approximate position of each element\n"
            "Be concise and clear. Respond in English only."
        )

        try:
            return _call_nvidia_vision(screenshot_path, prompt, max_tokens=500)
        except Exception as e:
            logger.warning("Vision summary failed: %s", e)
            return ""

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

    def _take_screenshot(self, page) -> str | None:
        """Take a screenshot and return the file path."""
        try:
            tmp = Path(tempfile.mkdtemp()) / "vision_screenshot.png"
            page.screenshot(path=str(tmp), full_page=False)
            return str(tmp)
        except Exception as e:
            logger.error("Screenshot failed: %s", e)
            return None

    def _parse_json(self, text: str) -> dict | None:
        """Extract and parse the first JSON object from text."""
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start:end + 1])
        except Exception:
            pass
        return None
