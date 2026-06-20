"""KRYTH Desktop Runtime Layer.

Acts as a translation boundary between the raw KRYTH agent core and the
Tauri desktop frontend. Raw agent events never reach the UI directly —
every event passes through ui_transformer before broadcast.

Architecture:
    KRYTH Core (agent_loop, tools, orchestration)
        ↕ EventBus (subscribe)
    event_router.py
        ↕ ui_transformer.py
    desktop_bridge.py (FastAPI / WebSocket)
        ↕ WebSocket ws://127.0.0.1:7765
    React Frontend

Modules:
    persistence     — SQLite via Python sqlite3
    session_manager — KRYTH session lifecycle + desktop metadata
    ui_transformer  — raw EventKind → UI-friendly event schema
    event_router    — BUS subscription + WS broadcast
    terminal_manager— PTY process lifecycle (pywinpty/ptyprocess)
    desktop_bridge  — FastAPI app with all endpoints
"""

from __future__ import annotations

__all__ = ["create_app", "start"]


def create_app():
    """Return the configured FastAPI application."""
    from kryth_desktop_runtime.desktop_bridge import build_app
    return build_app()


def start(host: str = "127.0.0.1", port: int = 7765) -> None:
    """Start the KRYTH Desktop bridge server (blocking)."""
    import uvicorn
    app = create_app()
    config = uvicorn.Config(app, host=host, port=port,
                            log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    original_startup = server.startup

    async def _signal_ready(sockets=None):
        await original_startup(sockets)
        print("READY", flush=True)

    server.startup = _signal_ready
    server.run()
