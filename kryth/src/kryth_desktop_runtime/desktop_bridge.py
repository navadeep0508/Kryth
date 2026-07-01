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
import os
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
    "KRYTH_SUMMARIZER_MODEL", "KRYTH_FAST_MODEL", "KRYTH_PROFILE",
    "KRYTH_EXEC_PROFILE", "KRYTH_NO_PERSIST", "KRYTH_ASSUME_YES",
    "KRYTH_READ_TIMEOUT", "KRYTH_LOG_DIR", "KRYTH_VISION_MODEL",
    "KRYTH_BROWSER_MODEL", "KRYTH_BROWSER_PROVIDER", "KRYTH_TTFT_TIMEOUT",
    "NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
]

# ---------------------------------------------------------------------------
# Pending approvals
# ---------------------------------------------------------------------------

_pending_approvals: dict[str, dict] = {}
_approval_lock = threading.Lock()


def _install_approval_intercept() -> None:
    """Monkey-patch the agent's permission and confirmation systems to route
    through the desktop WebSocket bridge instead of the CLI's Rich UI.

    Patches:
    - agent.io.confirm         — simple yes/no confirmations
    - agent.permissions.ask_user — tool permission (the "Action Approval Required" prompt)
    - agent.ui.permission_request — the interactive Rich panel (fallback)
    """
    import agent.io as _io
    import agent.permissions as _perms
    import agent.ui as _ui

    def _desktop_confirm(message: str, default: bool = False) -> bool:
        """Route simple confirmations through the desktop approval UI."""
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

        sid = session_manager.current_session_id()
        db.save_approval(sid or "", message, risk)

        granted = evt.wait(timeout=120)
        with _approval_lock:
            _pending_approvals.pop(aid, None)

        return result["approved"] if granted else default

    def _desktop_ask_user(tool: str, args: dict) -> str:
        """Route tool permission requests through the desktop approval UI.

        This replaces the CLI's interactive "Action Approval Required" panel.
        Returns 'allow', 'a' (allow always), or 'deny'.
        """
        try:
            from agent.env import getenv_bool
            if getenv_bool("KRYTH_ASSUME_YES"):
                return "allow"
        except Exception:
            pass

        # Build human-readable description
        sig = _perms._args_signature(tool, args)
        message = f"{tool}: {sig}"

        aid = str(uuid.uuid4())
        evt = threading.Event()
        result: dict = {"approved": True}

        with _approval_lock:
            _pending_approvals[aid] = {"event": evt, "result": result}

        # Determine risk level
        high_risk_words = ("delete", "remove", "drop", "format", "truncate",
                           "system32", "rm -rf", "rmdir", "force", "sudo")
        combined = f"{tool} {sig}".lower()
        risk = "high" if any(w in combined for w in high_risk_words) else "medium"

        event_router.broadcast_sync({
            "kind": "approval_request",
            "id":   aid,
            "ts":   0.0,
            "data": {
                "message": message,
                "risk": risk,
                "tool": tool,
                "default": True,
            },
        })

        sid = session_manager.current_session_id()
        db.save_approval(sid or "", message, risk)

        granted = evt.wait(timeout=120)
        with _approval_lock:
            _pending_approvals.pop(aid, None)

        if not granted:
            return "deny"
        if not result["approved"]:
            return "deny"
        # "allow always" → remember this tool as permanently allowed for session
        if result.get("always"):
            try:
                _perms.remember(tool, args, "allow")
            except Exception:
                pass
            return "allow"
        return "allow"

    def _desktop_permission_request(tool: str, signature: str) -> str:
        """Replaces the Rich interactive panel for permission requests."""
        # This is called by ui.permission_request() which is invoked inside
        # ask_user. Since we've already patched ask_user, this is a safety net.
        # Return 'y' (allow once) — the actual gating is in ask_user.
        return "y"

    # Apply patches
    _io.confirm = _desktop_confirm
    _perms.ask_user = _desktop_ask_user
    _ui.permission_request = _desktop_permission_request

    # Also patch the direct import in agent_loop (it uses `from X import Y`)
    try:
        import agent.agent_loop as _loop
        _loop.ask_user = _desktop_ask_user
    except (ImportError, AttributeError):
        pass


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
    always: bool = False


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
                "agent_running": session_manager.is_running(),
                "cwd": session_manager._current_project_path or os.getcwd()}

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
        # If no cwd from request, use stored project path
        if not cwd:
            cwd = session_manager._current_project_path or ""
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
        entry["result"]["always"] = req.always
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
        
        # Set env var immediately
        os.environ[req.key] = req.value
        try:
            from agent.env import setenv
            setenv(req.key, req.value)
        except ImportError:
            pass
        db.set_setting(req.key, req.value)

        # Also persist to ~/.kryth/config.json so it survives restarts
        _ENV_TO_CONFIG_KEY = {
            "API_KEY": "api_key",
            "NVIDIA_API_KEY": "api_key",
            "OPENAI_API_KEY": "api_key",
            "ANTHROPIC_API_KEY": "api_key",
            "GOOGLE_API_KEY": "api_key",
            "MODEL": "model",
            "KRYTH_MAIN_MODEL": "model",
            "KRYTH_BASE_URL": "base_url",
        }
        config_key = _ENV_TO_CONFIG_KEY.get(req.key)
        if config_key:
            try:
                import json as _json
                from pathlib import Path as _Path
                config_file = _Path.home() / ".kryth" / "config.json"
                config_file.parent.mkdir(parents=True, exist_ok=True)
                cfg = {}
                if config_file.exists():
                    cfg = _json.loads(config_file.read_text(encoding="utf-8"))
                cfg[config_key] = req.value
                config_file.write_text(_json.dumps(cfg, indent=2), encoding="utf-8")
            except Exception:
                pass

        # If a key that affects the LLM client changed, force-reload it
        _LLM_RELOAD_KEYS = {
            "KRYTH_BASE_URL", "KRYTH_MAIN_MODEL", "KRYTH_PLANNER_MODEL",
            "KRYTH_FAST_MODEL", "KRYTH_VISION_MODEL", "KRYTH_SUMMARIZER_MODEL",
            "OPENAI_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
        }
        if req.key in _LLM_RELOAD_KEYS:
            try:
                from agent.llm import reload_client
                reload_client()
            except Exception:
                pass

        return {"ok": True}

    @app.get("/api/models")
    async def list_models(base_url: str = "", api_key: str = ""):
        """Fetch available models from an OpenAI-compatible endpoint."""
        import httpx

        # Use provided or fall back to env
        if not base_url:
            base_url = os.environ.get("KRYTH_BASE_URL", "https://api.openai.com/v1")
        if not api_key:
            # Try all available keys
            api_key = (os.environ.get("OPENAI_API_KEY", "") or
                       os.environ.get("NVIDIA_API_KEY", "") or
                       os.environ.get("ANTHROPIC_API_KEY", "") or
                       os.environ.get("GOOGLE_API_KEY", ""))

        # If base URL looks like NVIDIA but we have no key from params, use NVIDIA key
        if "nvidia" in base_url.lower() and not api_key:
            api_key = os.environ.get("NVIDIA_API_KEY", "")
        # Also try NVIDIA key if the provided key doesn't start with expected prefix
        if "nvidia" in base_url.lower() and api_key and not api_key.startswith("nvapi-"):
            nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
            if nvidia_key:
                api_key = nvidia_key

        base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        # Try multiple endpoint patterns
        urls_to_try = []
        if base_url.endswith("/v1"):
            urls_to_try.append(f"{base_url}/models")
        elif "/v1" in base_url:
            urls_to_try.append(f"{base_url}/models")
        else:
            urls_to_try.append(f"{base_url}/v1/models")
            urls_to_try.append(f"{base_url}/models")

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                for models_url in urls_to_try:
                    try:
                        resp = await client.get(models_url, headers=headers)
                        if resp.status_code == 200:
                            data = resp.json()
                            # Handle various response formats
                            models_list = (
                                data.get("data") or
                                data.get("models") or
                                data.get("result") or
                                (data if isinstance(data, list) else [])
                            )
                            model_ids = sorted(set(
                                m.get("id", m.get("name", m.get("model", "")))
                                for m in models_list
                                if isinstance(m, dict) and (m.get("id") or m.get("name") or m.get("model"))
                            ))
                            if model_ids:
                                return {"models": model_ids[:200]}
                    except Exception:
                        continue

                return {"models": [], "error": "Not Found — this provider may not support /models endpoint. Type model name manually."}
        except Exception as e:
            return {"models": [], "error": str(e)[:200]}

    # ── Changes / Revert ─────────────────────────────────────────────────

    @app.get("/api/changes")
    async def get_changes():
        """List all files modified in this session (with snapshot available for revert)."""
        try:
            from agent.snapshots import list_all_snapshots
            snapshots = list_all_snapshots()
            return {"files": snapshots}
        except ImportError:
            # Fallback: scan graphify-out or git status
            try:
                import subprocess
                result = subprocess.run(
                    ["git", "diff", "--name-only", "HEAD"],
                    capture_output=True, text=True, timeout=5
                )
                files = [{"path": f, "status": "modified"} for f in result.stdout.strip().split("\n") if f]
                return {"files": files}
            except Exception:
                return {"files": []}

    @app.post("/api/revert")
    async def revert_file(path: str):
        """Revert a file to its pre-edit snapshot."""
        try:
            from agent.snapshots import restore
            success, msg = restore(path)
            if success:
                return {"ok": True, "message": msg}
            raise HTTPException(400, msg)
        except ImportError:
            raise HTTPException(500, "Snapshots module not available")

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

    # ── WebSocket: browser state stream ──────────────────────────────────

    @app.websocket("/ws/browser")
    async def ws_browser(ws: WebSocket):
        await ws.accept()
        from kryth_desktop_runtime import browser_bridge
        browser_bridge.set_event_loop(asyncio.get_running_loop())
        browser_bridge.register_browser_client(ws)
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive_text(), timeout=30)
                    data = json.loads(msg)
                    await browser_bridge.handle_browser_command(data)
                except asyncio.TimeoutError:
                    await ws.send_text(json.dumps({"type": "ping"}))
        except Exception:
            pass
        finally:
            browser_bridge.unregister_browser_client(ws)

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
