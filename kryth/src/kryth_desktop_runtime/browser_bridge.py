"""Browser Bridge — streams browser state to the desktop frontend.

Provides a WebSocket endpoint (/ws/browser) that:
1. Streams periodic screenshots of the browser viewport
2. Sends navigation state (URL, title, tabs)
3. Receives commands from the UI (navigate, back, forward, etc.)
4. Highlights agent actions in the frontend overlay

This allows the browser to appear "embedded" in the desktop app
even though it runs as a separate Chromium process controlled via CDP.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from typing import Any, Optional

_browser_ws_clients: set = set()
_browser_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_screenshot_task: Optional[asyncio.Task] = None
_streaming = False


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def register_browser_client(ws) -> None:
    global _streaming
    with _browser_lock:
        _browser_ws_clients.add(ws)
    # Start screenshot streaming when first client connects
    if not _streaming:
        _start_streaming()


def unregister_browser_client(ws) -> None:
    global _streaming
    with _browser_lock:
        _browser_ws_clients.discard(ws)
    if not _browser_ws_clients:
        _streaming = False


async def _broadcast_browser(payload: str) -> None:
    dead = []
    with _browser_lock:
        clients = list(_browser_ws_clients)
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    with _browser_lock:
        for ws in dead:
            _browser_ws_clients.discard(ws)


def broadcast_browser_sync(payload: dict) -> None:
    if _loop is None:
        return
    text = json.dumps(payload, default=str)
    asyncio.run_coroutine_threadsafe(_broadcast_browser(text), _loop)


def _start_streaming() -> None:
    """Screenshot streaming disabled — using real visible browser window."""
    global _streaming
    _streaming = False


async def _take_screenshot() -> Optional[str]:
    """Take a screenshot of the current browser page."""
    try:
        from agent.providers.browser_use_provider import _get_worker
        worker = _get_worker()

        async def _do():
            session = await worker._ensure_session()
            page = await session.get_current_page()
            if not page:
                return None
            return await page.screenshot()

        return await asyncio.wait_for(_do(), timeout=5)
    except Exception:
        return None


async def _get_browser_state() -> Optional[dict]:
    """Get current browser state (URL, title, tabs)."""
    try:
        from agent.providers.browser_use_provider import _get_worker
        worker = _get_worker()

        async def _do():
            session = await worker._ensure_session()
            url = await session.get_current_page_url()
            title = await session.get_current_page_title()
            tabs_raw = await session.get_tabs()
            tabs = []
            if tabs_raw:
                for i, t in enumerate(tabs_raw):
                    tabs.append({
                        "id": str(i),
                        "title": getattr(t, "title", ""),
                        "url": getattr(t, "url", ""),
                        "active": i == 0,  # first is current
                    })
            return {"url": url or "", "title": title or "", "tabs": tabs}

        return await asyncio.wait_for(_do(), timeout=5)
    except Exception:
        return None


async def handle_browser_command(data: dict) -> None:
    """Handle commands from the frontend browser panel.
    
    These run in a separate thread to avoid blocking the CDP connection.
    Only handle simple navigation commands — don't interfere with agent's CDP session.
    """
    cmd = data.get("command", "")
    if not cmd:
        return

    # Run browser commands in a thread to avoid asyncio conflicts
    import threading

    def _run_cmd():
        try:
            from agent.providers.browser_use_provider import (
                open_url, back,
            )
            if cmd == "navigate":
                url = data.get("url", "")
                if url:
                    open_url(url)
            elif cmd == "back":
                back()
        except Exception:
            pass

    if cmd in ("navigate", "back", "forward", "reload"):
        threading.Thread(target=_run_cmd, daemon=True).start()


def notify_agent_action(action: str) -> None:
    """Notify frontend about an agent browser action (for overlay display).
    Also signals the frontend to switch to the browser panel."""
    broadcast_browser_sync({"type": "action", "label": action})
    # Also broadcast via main event stream so UI can auto-switch to browser tab
    try:
        from kryth_desktop_runtime import event_router
        event_router.broadcast_sync({
            "kind": "browser.active",
            "id": "",
            "ts": 0.0,
            "data": {"action": action},
        })
    except Exception:
        pass


def notify_browser_navigation(url: str, title: str = "") -> None:
    """Notify frontend that the browser navigated to a new URL."""
    broadcast_browser_sync({"type": "navigation", "url": url})
    broadcast_browser_sync({"type": "state", "url": url, "title": title, "tabs": []})
    # Also trigger the embedded webview to navigate
    try:
        from kryth_desktop_runtime import event_router
        event_router.broadcast_sync({
            "kind": "browser.navigate",
            "id": "",
            "ts": 0.0,
            "data": {"url": url},
        })
    except Exception:
        pass
