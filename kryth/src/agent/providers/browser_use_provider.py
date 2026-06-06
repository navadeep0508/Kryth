"""browser_agent provider — thread-safe browser automation via browser-use.

All browser_agent.BrowserSession operations run in a dedicated background
asyncio thread, avoiding conflicts with prompt_toolkit's event loop.

Requires: browser_agent installed from kryth/src/agent/browser-use/
  cd kryth/src/agent/browser-use && pip install -e .
  playwright install chromium
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _err(msg: str) -> str:
    return f"[ERROR] {msg}"


# ---------------------------------------------------------------------------
# Ensure browser_agent is importable from browser-use/
# ---------------------------------------------------------------------------

def _ensure_path() -> bool:
    agent_dir = Path(__file__).resolve().parent.parent   # kryth/src/agent/
    browser_use_dir = agent_dir / "browser-use"
    path_str = str(browser_use_dir)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
    try:
        import browser_agent  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Thread-isolated asyncio worker
# ---------------------------------------------------------------------------

class _BrowserWorker:
    """Runs browser_agent.BrowserSession in a dedicated asyncio event loop thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            daemon=True,
            name="kryth-browser-use",
        )
        self._thread.start()
        self._session = None

    def run(self, coro, timeout: int = 60) -> Any:
        """Submit a coroutine to the background loop and return its result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _ensure_session(self):
        if self._session is not None:
            return self._session
        _ensure_path()
        from browser_agent import BrowserSession
        self._session = BrowserSession(headless=False)
        await self._session.start()
        return self._session

    def shutdown(self) -> None:
        async def _stop():
            if self._session:
                try:
                    await self._session.stop()
                except Exception:
                    pass
                self._session = None
        try:
            self.run(_stop(), timeout=10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


_worker: _BrowserWorker | None = None
_worker_lock = threading.Lock()


def _get_worker() -> _BrowserWorker:
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                _worker = _BrowserWorker()
    return _worker


def _run(coro, timeout: int = 60) -> Any:
    return _get_worker().run(coro, timeout=timeout)


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def ensure_available() -> str | None:
    if not _ensure_path():
        return (
            "browser_agent not found. Install: "
            "cd kryth/src/agent/browser-use && pip install -e . && playwright install chromium"
        )
    return None


# ---------------------------------------------------------------------------
# JS helper — safe for embedding CSS selectors
# ---------------------------------------------------------------------------

def _js_esc(s: str) -> str:
    """Escape a string for embedding in a JS double-quoted string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def open_url(url: str) -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            await session.navigate_to(url)
            return f"navigated to {url}"
        return _run(_do())
    except Exception as ex:
        return _err(f"open_url failed: {ex}")


def get_url() -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            return await session.get_current_page_url()
        return _run(_do())
    except Exception as ex:
        return _err(f"get_url failed: {ex}")


def get_title() -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            return await session.get_current_page_title()
        return _run(_do())
    except Exception as ex:
        return _err(f"get_title failed: {ex}")


def get_html() -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            return await page.evaluate("() => document.documentElement.outerHTML")
        return _run(_do())
    except Exception as ex:
        return _err(f"get_html failed: {ex}")


def state() -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            url = await session.get_current_page_url()
            title = await session.get_current_page_title()
            return f"URL: {url}\nTitle: {title}\n"
        return _run(_do())
    except Exception as ex:
        return _err(f"state failed: {ex}")


def back() -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            await page.evaluate("() => window.history.back()")
            return "navigated back"
        return _run(_do())
    except Exception as ex:
        return _err(f"back failed: {ex}")


# ---------------------------------------------------------------------------
# Interaction
# ---------------------------------------------------------------------------

def click(selector: str) -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            candidates = [selector]
            if selector in ("button[type=submit]", "submit", "button"):
                candidates += ["input[type=submit]", "button:last-of-type"]
            for sel in candidates:
                esc = _js_esc(sel)
                clicked = await page.evaluate(
                    f'() => {{ const el = document.querySelector("{esc}"); if (el) {{ el.click(); return true; }} return false; }}'
                )
                if clicked:
                    return f"clicked {sel}"
            return _err(f"selector '{selector}' matched 0 elements")
        return _run(_do())
    except Exception as ex:
        return _err(f"click failed: {ex}")


def type_text(selector: str, text: str) -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            esc_sel = _js_esc(selector)
            esc_text = _js_esc(text)
            found = await page.evaluate(
                f'() => {{'
                f'  const el = document.querySelector("{esc_sel}");'
                f'  if (!el) return false;'
                f'  el.focus();'
                f'  el.value = "{esc_text}";'
                f'  el.dispatchEvent(new Event("input", {{bubbles: true}}));'
                f'  el.dispatchEvent(new Event("change", {{bubbles: true}}));'
                f'  return true;'
                f'}}'
            )
            if not found:
                return _err(f"'{selector}' not found")
            return f"typed into {selector}"
        return _run(_do())
    except Exception as ex:
        return _err(f"type_text failed: {ex}")


def fill(selector: str, value: str) -> str:
    return type_text(selector, value)


def select(selector: str, value: str) -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            esc_sel = _js_esc(selector)
            esc_val = _js_esc(value)
            found = await page.evaluate(
                f'() => {{'
                f'  const el = document.querySelector("{esc_sel}");'
                f'  if (!el) return false;'
                f'  el.value = "{esc_val}";'
                f'  el.dispatchEvent(new Event("change", {{bubbles: true}}));'
                f'  return true;'
                f'}}'
            )
            if not found:
                return _err(f"'{selector}' not found")
            return f"selected {value} in {selector}"
        return _run(_do())
    except Exception as ex:
        return _err(f"select failed: {ex}")


def keys(combo: str) -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            await page.press(combo)
            return f"sent {combo}"
        return _run(_do())
    except Exception as ex:
        return _err(f"keys failed: {ex}")


def scroll(direction: str = "down") -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            dx, dy = {"down": (0, 800), "up": (0, -800), "right": (800, 0), "left": (-800, 0)}.get(
                direction, (0, 800)
            )
            await page.evaluate(f"() => window.scrollBy({dx}, {dy})")
            return f"scrolled {direction}"
        return _run(_do())
    except Exception as ex:
        return _err(f"scroll failed: {ex}")


def eval_js(expression: str) -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            stripped = expression.strip()
            # browser_agent's Page.evaluate() requires arrow-function format
            # AND must return a defined value (undefined causes CDP exception).
            # We wrap the expression so void-returning calls (scrollTo, click,
            # dispatchEvent, etc.) always resolve to a string result.
            if stripped.startswith("(") and "=>" in stripped:
                # Already arrow format — ensure it returns something
                expr = f"() => {{ const __r = ({stripped})(); return __r !== undefined ? String(__r) : 'ok'; }}"
            else:
                # Wrap bare expression; void calls return 'ok'
                expr = f"() => {{ const __r = (function(){{ {stripped} }})(); return __r !== undefined ? String(__r) : 'ok'; }}"
            return await page.evaluate(expr)
        return _run(_do())
    except Exception as ex:
        return _err(f"eval_js failed: {ex}")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(selector: str = "") -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            if not selector or selector in ("body", "*"):
                return await page.evaluate(
                    "() => document.body ? (document.body.innerText || document.body.textContent || '') : ''"
                )
            esc = _js_esc(selector)
            return await page.evaluate(
                f'() => {{'
                f'  const els = document.querySelectorAll("{esc}");'
                f'  return JSON.stringify([...els].map(el => (el.innerText || el.textContent || "").trim()).filter(Boolean));'
                f'}}'
            )
        return _run(_do())
    except Exception as ex:
        return _err(f"extract failed: {ex}")


# ---------------------------------------------------------------------------
# Screenshot
# ---------------------------------------------------------------------------

def screenshot() -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            page = await session.get_current_page()
            if not page:
                return _err("no page open")
            b64_data = await page.screenshot()
            tmp = Path(tempfile.mkdtemp()) / "screenshot.png"
            tmp.write_bytes(base64.b64decode(b64_data))
            return str(tmp)
        return _run(_do())
    except Exception as ex:
        return _err(f"screenshot failed: {ex}")


# ---------------------------------------------------------------------------
# Tab management
# ---------------------------------------------------------------------------

def tab_list() -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            tabs = await session.get_tabs()
            if not tabs:
                return "[]"
            return "\n".join(
                f"[{i}] {getattr(t, 'title', '')} - {getattr(t, 'url', '')}"
                for i, t in enumerate(tabs)
            )
        return _run(_do())
    except Exception as ex:
        return _err(f"tab_list failed: {ex}")


def tab_new(url: str = "") -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            if url:
                await session.new_page(url)
                return f"opened new tab with {url}"
            await session.new_page()
            return "opened new tab"
        return _run(_do())
    except Exception as ex:
        return _err(f"tab_new failed: {ex}")


def tab_select(target_id: str) -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            tabs = await session.get_tabs()
            if target_id.isdigit():
                idx = int(target_id)
                if 0 <= idx < len(tabs):
                    tab = tabs[idx]
                    tab_id = getattr(tab, "tab_id", getattr(tab, "id", None))
                    if tab_id is not None:
                        from browser_agent.browser.events import SwitchTabEvent
                        await session.on_SwitchTabEvent(SwitchTabEvent(tab_id=tab_id))
                        return f"switched to tab [{idx}]"
            for tab in tabs:
                url = getattr(tab, "url", "")
                title = getattr(tab, "title", "")
                if target_id in url or target_id in title:
                    tab_id = getattr(tab, "tab_id", getattr(tab, "id", None))
                    if tab_id is not None:
                        from browser_agent.browser.events import SwitchTabEvent
                        await session.on_SwitchTabEvent(SwitchTabEvent(tab_id=tab_id))
                        return f"switched to tab matching '{target_id}'"
            return _err(f"tab '{target_id}' not found")
        return _run(_do())
    except Exception as ex:
        return _err(f"tab_select failed: {ex}")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download(url: str, output_dir: str = ".") -> str:
    e = ensure_available()
    if e:
        return e
    try:
        async def _do():
            session = await _get_worker()._ensure_session()
            os.makedirs(output_dir, exist_ok=True)
            await session.navigate_to(url)
            return f"opened {url}"
        return _run(_do())
    except Exception as ex:
        return _err(f"download failed: {ex}")


# ---------------------------------------------------------------------------
# LLM factory — mirrors run_nvidia_agent.py pattern for all providers
# ---------------------------------------------------------------------------

def _create_llm(provider: str, model_name: str):
    """Create an LLM from browser_agent, mirroring run_nvidia_agent.py exactly.

    Returns the LLM instance or an error string starting with '[ERROR]'.

    For NVIDIA: uses ChatNVIDIA (same as run_nvidia_agent.py) which sends the
    correct NVIDIA NIM parameters (max_tokens, temperature, top_p) and supports
    response_format json_schema for models that need structured output
    (meta/llama-3.2-90b-vision-instruct etc.).

    Auto-routing: if the resolved API key starts with 'nvapi-', the provider is
    forced to 'nvidia' regardless of what was requested, preventing NVIDIA keys
    from being sent to OpenAI's servers.
    """
    _ensure_path()
    try:
        nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
        openai_key = os.environ.get("OPENAI_API_KEY", "")

        # NVIDIA auto-routing: if the key looks like an NVIDIA key, use ChatNVIDIA
        resolved_key = openai_key if provider == "openai" else nvidia_key
        if resolved_key.startswith("nvapi-") or provider == "nvidia":
            if not nvidia_key:
                return _err("NVIDIA_API_KEY not set in environment")
            # Use ChatNVIDIA — exactly what run_nvidia_agent.py uses.
            # It sends max_tokens/temperature/top_p (correct NIM params) and
            # supports response_format json_schema for llama/vision models.
            from browser_agent import ChatNVIDIA
            return ChatNVIDIA(
                model=model_name,
                api_key=nvidia_key,
                temperature=0.7,
                max_tokens=4096,
            )

        if provider == "openai":
            from browser_agent import ChatOpenAI
            if not openai_key:
                return _err("OPENAI_API_KEY not set in environment")
            return ChatOpenAI(model=model_name, api_key=openai_key, temperature=0.2)

        if provider == "anthropic":
            from browser_agent import ChatAnthropic
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                return _err("ANTHROPIC_API_KEY not set in environment")
            return ChatAnthropic(model=model_name, api_key=api_key, max_tokens=8192)

        if provider == "google":
            from browser_agent import ChatGoogle
            api_key = os.environ.get("GOOGLE_API_KEY")
            if not api_key:
                return _err("GOOGLE_API_KEY not set in environment")
            return ChatGoogle(model=model_name, api_key=api_key, max_output_tokens=8096)

        if provider == "ollama":
            from browser_agent import ChatOllama
            return ChatOllama(model=model_name)

        return _err(f"unsupported provider '{provider}'. Use: nvidia, openai, anthropic, google, ollama")
    except ImportError as ex:
        return _err(f"failed to import {provider} LLM: {ex}")


# ---------------------------------------------------------------------------
# AI-driven browser task — mirrors run_nvidia_agent.py exactly
# ---------------------------------------------------------------------------

def browser_task(
    task: str,
    llm_provider: str = "nvidia",
    model_name: str = "stepfun-ai/step-3.7-flash",
    max_steps: int = 10,
    headless: bool = False,
    use_vision: bool = True,
) -> str:
    """Execute a natural language browser task via browser_agent.

    Runs in a dedicated thread with its own asyncio.run() — exactly matching
    run_nvidia_agent.py — so the httpx AsyncClient and BrowserSession CDP
    connections all live in one clean, isolated event loop with no conflicts
    from the persistent _BrowserWorker loop used for primitive operations.
    """
    e = ensure_available()
    if e:
        return e

    _ensure_path()

    result_holder: list = []

    def _run_in_fresh_thread() -> None:
        import asyncio as _asyncio

        async def _main():
            from browser_agent import Agent, BrowserSession
            from browser_agent.agent.views import AgentSettings
            from browser_agent.llm.nvidia.chat import ChatNVIDIA

            nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
            openai_key = os.environ.get("OPENAI_API_KEY", "")
            resolved_key = openai_key if llm_provider == "openai" else nvidia_key

            if resolved_key.startswith("nvapi-") or llm_provider == "nvidia":
                if not nvidia_key:
                    return _err("NVIDIA_API_KEY not set in environment")
                llm = ChatNVIDIA(
                    model=model_name,
                    api_key=nvidia_key,
                    temperature=0.7,
                    max_tokens=4096,
                )
            else:
                # For non-NVIDIA providers fall back to _create_llm
                llm = _create_llm(llm_provider, model_name)
                if isinstance(llm, str):
                    return llm

            os.makedirs("conversations", exist_ok=True)

            browser = BrowserSession(headless=headless)
            settings = AgentSettings(
                max_steps=max_steps,
                use_vision=use_vision,
                save_conversation_path="conversations/kryth_browser_agent",
            )

            agent = Agent(task=task, llm=llm, browser=browser, settings=settings)

            try:
                history = await agent.run()
                steps = len(history)
                final = history.final_result()
                return f"Completed in {steps} step{'s' if steps != 1 else ''}.\n{final or 'Done.'}"
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass

        try:
            result_holder.append(_asyncio.run(_main()))
        except Exception as ex:
            result_holder.append(_err(f"browser_task failed: {ex}"))

    t = threading.Thread(target=_run_in_fresh_thread, daemon=True, name="kryth-browser-task")
    t.start()
    t.join(timeout=310)

    if not result_holder:
        return _err("browser_task timed out after 310 seconds")
    return result_holder[0] or "Done."


# ---------------------------------------------------------------------------
# Compatibility shim (referenced by providers.playwright_browser users)
# ---------------------------------------------------------------------------

def _get_page():
    """Compatibility shim — direct page access not available with browser_agent."""
    return None
