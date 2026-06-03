"""Playwright browser manager — thread-isolated to avoid asyncio conflicts.

Playwright sync_api uses asyncio.run() internally. prompt_toolkit also runs
an asyncio event loop. Running both in the same thread causes:
  RuntimeError: asyncio.run() cannot be called from a running event loop

Fix: all Playwright operations run in a dedicated daemon thread that has
its own event loop, completely isolated from the main thread. The main
thread communicates via a thread-safe call queue.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thread-isolated Playwright worker
# ---------------------------------------------------------------------------

class _BrowserWorker:
    """Runs Playwright in a dedicated thread with its own event loop."""

    def __init__(self) -> None:
        self._call_queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="kryth-browser",
            daemon=True,
        )
        self._started = False
        self._ready = threading.Event()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()
        self._ready.wait(timeout=30)

    def _run(self) -> None:
        """Worker thread main loop — owns all Playwright objects."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                self._playwright = pw
                self._ready.set()
                while True:
                    try:
                        fn, result_box = self._call_queue.get(timeout=1)
                        if fn is None:  # shutdown sentinel
                            break
                        try:
                            result_box["result"] = fn(self)
                        except Exception as e:
                            result_box["error"] = e
                        finally:
                            result_box["done"].set()
                    except queue.Empty:
                        continue
        except Exception as e:
            result_box = {"done": threading.Event(), "error": e}
            self._ready.set()  # unblock waiter even on failure

    def call(self, fn: Callable) -> Any:
        """Execute fn(worker) in the browser thread and return the result."""
        self.start()
        result_box: dict = {"done": threading.Event(), "result": None, "error": None}
        self._call_queue.put((fn, result_box))
        result_box["done"].wait(timeout=60)
        if result_box["error"] is not None:
            raise result_box["error"]
        return result_box["result"]

    def shutdown(self) -> None:
        if not self._started:
            return
        # Save profile state before closing
        try:
            self.call(lambda w: w._save_state_if_needed())
        except Exception:
            pass
        self._call_queue.put((None, {"done": threading.Event()}))
        self._thread.join(timeout=5)

    # ---- Internal browser operations (run inside worker thread) ----

    def _ensure_browser(self, headless: bool, user_agent: str, viewport: dict) -> None:
        if self._browser and self._browser.is_connected():
            return
        args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-setuid-sandbox",
        ]
        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=args,
        )
        self._context = self._browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            ignore_https_errors=True,
        )
        self._page = self._context.new_page()

    def _ensure_page(self) -> Any:
        if self._page is not None:
            try:
                self._page.title()  # health check
                return self._page
            except Exception:
                pass
        if self._context:
            self._page = self._context.new_page()
        return self._page

    def _save_state_if_needed(self) -> None:
        if self._context and hasattr(self, "_profile_state_path") and self._profile_state_path:
            try:
                self._context.storage_state(path=self._profile_state_path)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Global singleton worker
# ---------------------------------------------------------------------------

_worker: _BrowserWorker | None = None
_worker_lock = threading.Lock()

_DEFAULT_HEADLESS = False
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
_DEFAULT_VIEWPORT = {"width": 1280, "height": 720}


def _get_worker() -> _BrowserWorker:
    global _worker
    if _worker is None:
        with _worker_lock:
            if _worker is None:
                _worker = _BrowserWorker()
    return _worker


def _browser_call(fn: Callable) -> Any:
    """Run fn in the browser thread, auto-ensuring the browser is ready."""
    w = _get_worker()

    def _wrapped(worker: _BrowserWorker):
        worker._ensure_browser(
            _DEFAULT_HEADLESS,
            _DEFAULT_USER_AGENT,
            _DEFAULT_VIEWPORT,
        )
        return fn(worker)

    return w.call(_wrapped)


# ---------------------------------------------------------------------------
# Public API — same interface as before but thread-safe
# ---------------------------------------------------------------------------

class BrowserManager:
    """Thread-safe browser manager. All operations dispatched to browser thread."""

    @staticmethod
    def is_available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except ImportError:
            return False

    def get_page(self):
        return _browser_call(lambda w: w._ensure_page())

    def new_tab(self, url: str = ""):
        def _fn(w):
            page = w._context.new_page()
            w._page = page
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return page
        return _browser_call(_fn)

    def list_tabs(self) -> list[dict]:
        def _fn(w):
            tabs = []
            if w._browser:
                for ctx in w._browser.contexts:
                    for i, p in enumerate(ctx.pages):
                        try:
                            tabs.append({"index": i, "title": p.title(), "url": p.url})
                        except Exception:
                            tabs.append({"index": i, "title": "(closed)", "url": ""})
            return tabs
        return _browser_call(_fn)

    def switch_tab(self, target) -> bool:
        def _fn(w):
            if not w._browser:
                return False
            all_pages = []
            for ctx in w._browser.contexts:
                all_pages.extend(ctx.pages)
            idx = None
            if isinstance(target, int):
                idx = target
            elif isinstance(target, str) and target.isdigit():
                idx = int(target)
            if idx is not None and 0 <= idx < len(all_pages):
                w._page = all_pages[idx]
                try:
                    w._page.bring_to_front()
                except Exception:
                    pass
                return True
            if isinstance(target, str):
                for p in all_pages:
                    try:
                        if target in p.url or target in p.title():
                            w._page = p
                            try:
                                p.bring_to_front()
                            except Exception:
                                pass
                            return True
                    except Exception:
                        continue
            return False
        return _browser_call(_fn)

    def save_profile_state(self, path: str) -> bool:
        def _fn(w):
            if w._context:
                try:
                    w._context.storage_state(path=path)
                    return True
                except Exception:
                    return False
            return False
        return _browser_call(_fn)

    def cleanup(self) -> None:
        global _worker
        if _worker is not None:
            _worker.shutdown()
            _worker = None


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------

_manager_instance: BrowserManager | None = None


def get_browser(headless: bool = False, profile: str = "") -> BrowserManager:
    global _manager_instance, _DEFAULT_HEADLESS
    if headless:
        _DEFAULT_HEADLESS = True
    if _manager_instance is None:
        _manager_instance = BrowserManager()
    return _manager_instance


def get_page():
    """Get the current page from the global browser (thread-safe)."""
    return get_browser().get_page()


def cleanup() -> None:
    global _manager_instance
    if _manager_instance is not None:
        _manager_instance.cleanup()
        _manager_instance = None
