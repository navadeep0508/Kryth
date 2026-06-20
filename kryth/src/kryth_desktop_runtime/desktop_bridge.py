"""KRYTH Desktop Bridge — FastAPI application.

All HTTP and WebSocket endpoints that the Tauri frontend talks to.
Business logic is delegated to the other runtime modules; this file
is ONLY a thin HTTP adapter.

Endpoints:
    GET  /health
    GET  /api/sessions                   — recent sessions
    GET  /api/projects                   — recent projects
    POST /api/agent/run                  — start a task
    POST /api/agent/stop                 — interrupt current task
    POST /api/approve    {id, approved}  — resolve a pending approval
    GET  /api/config                     — all KRYTH_* env vars
    PATCH /api/config    {key, value}    — set one env var
    GET  /api/files      ?path=.         — directory listing
    GET  /api/file       ?path=          — read file
    POST /api/file       {path, content} — write file
    GET  /api/history    ?session_id=    — chat message history
    WS   /ws/events                      — UI event stream
    WS   /ws/shell                       — xterm.js PTY
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError as exc:
    raise ImportError(
        "Install with: pip install fastapi uvicorn"
    ) from exc

from pydantic import BaseModel

from kryth_desktop_runtime import event_router, session_manager
from kryth_desktop_runtime import persistence as db

# ---------------------------------------------------------------------------
# Config key whitelist
# ---------------------------------------------------------------------------

_CONFIG_KEYS = [
    "KRYTH_BASE_URL", "KRYTH_MAIN_MODEL", "KRYTH_PLANNER_MODEL",
    "KRYTH_FAST_MODEL", "KRYTH_PROFILE", "KRYTH_EXEC_PROFILE",
    "KRYTH_NO_PERSIST", "KRYTH_ASSUME_YES", "KRYTH_READ_TIMEOUT",
    "KRYTH_LOG_DIR",
]

# ---------------------------------------------------------------------------
# Pending approvals
# ---------------------------------------------------------------------------

_pending_approvals: dict[str, dict] = {}
_approval_lock = threading.Lock()


def _install_approval_intercept() -> None:
    """Monkey-patch agent.io.confirm to route through the desktop bridge."""
    import agent.io as _io

    def _desktop_confirm(message: str, default: bool = False) -> bool:
        try:
            from agent.env import getenv_bool
            if getenv_bool("KRYTH_ASSUME_YES"):
                return True
        except Exception:
            pass

        aid = str(uuid.uuid4())
        evt = threading.Event()
        result: dict = {"approved": default}

        with _approval_lock:
            _pending_approvals[aid] = {"event": evt, "result": result}

        risk = "high" if any(w in message.lower()
                             for w in ("delete", "remove", "drop", "system32",
                                       "format", "truncate")) else "medium"

        event_router.broadcast_sync({
            "kind": "approval_request",
            "id":   aid,
            "ts":   0.0,
            "data": {"message": message, "risk": risk, "default": default},
        })

        # Persist to DB
        sid = session_manager.current_session_id()
        db.save_approval(sid or "", message, risk)

        granted = evt.wait(timeout=120)
        with _approval_lock:
            _pending_approvals.pop(aid, None)

        return result["approved"] if granted else default

    _io.confirm = _desktop_confirm


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    user_input: str
    cwd: str = ""
    project_path: str = ""


class ApproveRequest(BaseModel):
    id: str
    approved: bool


class ConfigPatchRequest(BaseModel):
    key: str
    value: str


class FileWriteRequest(BaseModel):
    path: str
    content: str


# ---------------------------------------------------------------------------
# Build FastAPI app
# ---------------------------------------------------------------------------

def build_app() -> FastAPI:
    app = FastAPI(title="KRYTH Desktop", version="2.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup():
        event_router.set_event_loop(asyncio.get_running_loop())
        event_router.install()
        _install_approval_intercept()

    # ── REST ──────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"ok": True, "version": "2.0.0",
                "agent_running": session_manager.is_running()}

    @app.get("/api/sessions")
    async def sessions():
        return {"sessions": session_manager.get_recent_sessions()}

    @app.get("/api/projects")
    async def projects():
        return {"projects": session_manager.get_recent_projects()}

    @app.get("/api/history")
    async def history(session_id: str = ""):
        return {"messages": session_manager.get_history(session_id or None)}

    @app.post("/api/agent/run")
    async def agent_run(req: RunRequest):
        if session_manager.is_running():
            raise HTTPException(409, "Agent already running")
        cwd = req.cwd or req.project_path or ""
        session_manager.start_session(project_path=cwd)
        t = threading.Thread(
            target=session_manager.run_agent,
            args=(req.user_input, cwd),
            daemon=True,
        )
        t.start()
        return {"ok": True}

    @app.post("/api/agent/stop")
    async def agent_stop():
        session_manager.interrupt()
        return {"ok": True}

    @app.post("/api/approve")
    async def approve(req: ApproveRequest):
        with _approval_lock:
            entry = _pending_approvals.get(req.id)
        if entry is None:
            raise HTTPException(404, "Approval not found")
        entry["result"]["approved"] = req.approved
        entry["event"].set()
        db.resolve_approval(req.id, req.approved)
        return {"ok": True}

    @app.get("/api/config")
    async def config_get():
        try:
            from agent.env import getenv
        except ImportError:
            import os
            getenv = lambda k: os.environ.get(k, "")
        return {k: getenv(k) or "" for k in _CONFIG_KEYS}

    @app.patch("/api/config")
    async def config_patch(req: ConfigPatchRequest):
        if req.key not in _CONFIG_KEYS:
            raise HTTPException(400, f"Unknown key: {req.key}")
        try:
            from agent.env import setenv
            setenv(req.key, req.value)
        except ImportError:
            import os
            os.environ[req.key] = req.value
        db.set_setting(req.key, req.value)
        return {"ok": True}

    @app.get("/api/files")
    async def list_files(path: str = "."):
        target = Path(path).resolve()
        if not target.exists():
            raise HTTPException(404, "Path not found")
        if not target.is_dir():
            raise HTTPException(400, "Not a directory")
        try:
            entries = sorted(
                target.iterdir(),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
            return {
                "path": str(target),
                "entries": [
                    {
                        "name": e.name,
                        "path": str(e),
                        "is_dir": e.is_dir(),
                        "size": e.stat().st_size if e.is_file() else 0,
                    }
                    for e in entries
                ],
            }
        except PermissionError:
            raise HTTPException(403, "Permission denied")

    @app.get("/api/file")
    async def read_file(path: str):
        target = Path(path).resolve()
        if not target.is_file():
            raise HTTPException(404, "File not found")
        try:
            return {"path": str(target),
                    "content": target.read_text("utf-8", errors="replace")}
        except PermissionError:
            raise HTTPException(403, "Permission denied")

    @app.post("/api/file")
    async def write_file(req: FileWriteRequest):
        target = Path(req.path).resolve()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(req.content, encoding="utf-8")
        except PermissionError:
            raise HTTPException(403, "Permission denied")
        return {"ok": True, "path": str(target)}

    # ── WebSocket: event stream ──────────────────────────────────────────

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket):
        await ws.accept()
        event_router.register_client(ws)
        await ws.send_text(json.dumps({
            "kind": "connection_ready",
            "id": str(uuid.uuid4()),
            "ts": 0.0,
            "data": {"version": "2.0.0"},
        }))
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive_text(), timeout=25)
                    parsed = json.loads(msg)
                    if parsed.get("kind") == "ping":
                        await ws.send_text(json.dumps({"kind": "pong"}))
                except asyncio.TimeoutError:
                    await ws.send_text(json.dumps({"kind": "ping"}))
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            event_router.unregister_client(ws)

    # ── WebSocket: shell PTY ─────────────────────────────────────────────

    @app.websocket("/ws/shell")
    async def ws_shell(ws: WebSocket):
        await ws.accept()
        loop = asyncio.get_running_loop()

        async def _send(data: bytes):
            try:
                await ws.send_bytes(data)
            except Exception:
                pass

        def _on_output(data: bytes):
            asyncio.run_coroutine_threadsafe(_send(data), loop)

        from kryth_desktop_runtime.terminal_manager import TerminalSession
        term = TerminalSession(on_output=_on_output)
        term.start()

        try:
            while True:
                data = await ws.receive_bytes()
                # Control messages: JSON {"type":"resize","cols":N,"rows":N}
                try:
                    ctrl = json.loads(data)
                    if ctrl.get("type") == "resize":
                        term.resize(ctrl.get("cols", 220), ctrl.get("rows", 50))
                    continue
                except (ValueError, UnicodeDecodeError):
                    pass
                term.write(data)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            term.kill()

    return app
