"""Event Router — bridges KRYTH EventBus to desktop WebSocket clients.

Flow:
    BUS.emit(EventKind, **data)
        → _on_raw_event()
            → ui_transformer.transform()
                → _broadcast_sync()  [asyncio thread-safe]
                    → WebSocket clients (frontend)

The router runs entirely in the agent thread's synchronous context.
Asyncio bridging uses run_coroutine_threadsafe so it's safe to call
from any thread.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Callable

from kryth_desktop_runtime import ui_transformer

# WebSocket client registry — managed by desktop_bridge
_ws_clients: set = set()
_ws_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_installed = False


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def register_client(ws) -> None:
    with _ws_lock:
        _ws_clients.add(ws)


def unregister_client(ws) -> None:
    with _ws_lock:
        _ws_clients.discard(ws)


async def _broadcast(payload: str) -> None:
    dead = []
    with _ws_lock:
        clients = list(_ws_clients)
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    with _ws_lock:
        for ws in dead:
            _ws_clients.discard(ws)


def broadcast_sync(payload: dict) -> None:
    """Broadcast from any thread (agent thread, timer, etc.)."""
    if _loop is None:
        return
    text = json.dumps(payload, default=str)
    asyncio.run_coroutine_threadsafe(_broadcast(text), _loop)


def _on_raw_event(event) -> None:
    kind  = event.kind.value if hasattr(event.kind, "value") else str(event.kind)
    eid   = getattr(event, "id", "")
    ts    = getattr(event, "ts", 0.0)
    data  = getattr(event, "data", {}) or {}

    ui_events = ui_transformer.transform(kind, eid, ts, data)
    for ue in ui_events:
        broadcast_sync(ue)


def install() -> None:
    """Subscribe to the KRYTH EventBus. Call once at server startup."""
    global _installed
    if _installed:
        return
    _installed = True

    from agent.ui.events import BUS
    BUS.subscribe(_on_raw_event)
