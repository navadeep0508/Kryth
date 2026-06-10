"""OpenAI browser provider adapter — via chat.openai.com.

This is the browser-based fallback for OpenAI when no API key is available.
Uses the ChatGPT web UI with network interception for streaming.

Authentication flow:
  1. Open https://chat.openai.com in a persistent Chromium profile
  2. User logs in with their OpenAI account (one-time)
  3. Session is saved and reused

Note: If you have an OpenAI API key, use the standard API provider instead
(set OPENAI_API_KEY and KRYTH_BASE_URL=https://api.openai.com/v1).
This browser provider is for users without API access.

For personal/educational use only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from agent.bridge.providers.base import BaseProvider, ProviderError

logger = logging.getLogger("aicoder.bridge.openai")

CHATGPT_URL = "https://chat.openai.com"
LOGIN_CHECK_SELECTOR = "[data-testid='profile-button'], nav [href='/profile'], .user-avatar"

AVAILABLE_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4",
    "o1",
    "o1-mini",
]


class OpenAIBrowserProvider(BaseProvider):
    """Browser-based OpenAI provider via ChatGPT web UI."""

    name = "openai"
    default_model = "gpt-4o"
    base_url = CHATGPT_URL

    def __init__(self, headless: bool = False):
        super().__init__(headless=headless)
        self._model = self.default_model

    async def is_ready(self) -> bool:
        """Check if we're logged in to ChatGPT."""
        try:
            await self._page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=15000)
            if "auth" in self._page.url or "login" in self._page.url:
                return False
            element = await self._page.query_selector(LOGIN_CHECK_SELECTOR)
            return element is not None
        except Exception as e:
            logger.debug(f"[openai] is_ready check failed: {e}")
            return False

    async def authenticate(self) -> None:
        """Open ChatGPT and wait for the user to log in."""
        logger.info("[openai] opening browser for authentication...")
        print("\n  [bridge] Opening ChatGPT in your browser.")
        print("  Please log in with your OpenAI account.")
        print("  The bridge will continue automatically once login is detected.\n")

        await self._page.goto(CHATGPT_URL, wait_until="domcontentloaded")

        try:
            await self._page.wait_for_selector(
                LOGIN_CHECK_SELECTOR,
                timeout=180_000,
            )
            logger.info("[openai] authentication successful")
            print("  [bridge] ✓ OpenAI authentication successful. Session saved.\n")

            from agent.bridge.session_store import save_meta
            save_meta("openai", {"authenticated": True, "model": self._model})

        except Exception:
            raise ProviderError(
                "OpenAI authentication timed out. "
                "Please run /bridge auth openai to try again.",
                retryable=True,
            )

    async def list_models(self) -> list[str]:
        return AVAILABLE_MODELS

    async def chat_stream(
        self,
        messages: list[dict],
        model: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Send a prompt to ChatGPT and stream the response.

        Intercepts ChatGPT's internal backend API streaming endpoint.
        """
        if model:
            self._model = model

        prompt = self._messages_to_text(messages)
        response_tokens: list[str] = []
        done_event = asyncio.Event()
        error: list[Exception] = []

        async def handle_response(response):
            """Intercept ChatGPT's streaming backend API."""
            if "/backend-api/conversation" not in response.url:
                return
            if response.status != 200:
                return
            try:
                body = await response.text()
                for line in body.splitlines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        done_event.set()
                        continue
                    try:
                        data = json.loads(data_str)
                        # ChatGPT SSE format
                        message = data.get("message", {})
                        content = message.get("content", {})
                        parts = content.get("parts", [])
                        for part in parts:
                            if isinstance(part, str) and part:
                                # Only yield the delta (new characters)
                                # ChatGPT sends the full accumulated text each time
                                # We track position to extract only new content
                                response_tokens.append(part)
                    except json.JSONDecodeError:
                        pass
            except Exception as e:
                error.append(e)
                done_event.set()

        self._page.on("response", handle_response)

        try:
            await self._page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=20000)

            # Find the message input
            input_selector = "textarea[data-id], #prompt-textarea, textarea[placeholder]"
            await self._wait_for_selector(input_selector, timeout=10.0)

            await self._page.fill(input_selector, prompt)
            await self._page.keyboard.press("Enter")

            try:
                await asyncio.wait_for(done_event.wait(), timeout=90.0)
            except asyncio.TimeoutError:
                raise ProviderError("OpenAI response timed out", retryable=True)

            if error:
                raise ProviderError(f"OpenAI stream error: {error[0]}", retryable=True)

            # ChatGPT sends full accumulated text — extract only the final response
            if response_tokens:
                # The last token contains the complete response
                yield response_tokens[-1]

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"OpenAI chat failed: {e}", retryable=False)
        finally:
            self._page.remove_listener("response", handle_response)
