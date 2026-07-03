"""KRYTH Desktop Bridge Server.

Exposes the existing KRYTH runtime over WebSocket + REST so that the
Tauri desktop frontend can:

    1. Submit tasks  (POST /api/agent/run)
    2. Stream events (WS  /ws/events)
    3. Approve tools (POST /api/approve)
    4. Browse files  (GET /api/files, /api/file)
    5. Read / write config  (GET/PATCH /api/config)
    6. Run a shell pty  (WS /ws/shell)

All heavy logic stays in the existing KRYTH agent runtime.
This file is ONLY a thin HTTP/WS adapter.

Run in development:
    python -m kryth.desktop_main

The server binds to 127.0.0.1:7765 (loopback-only).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError as exc:
    raise ImportError(
        "Desktop server requires fastapi and uvicorn.\n"
        "Install with: pip install fastapi uvicorn"
    ) from exc

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Lazy-import KRYTH runtime (so this module can be imported before path is set)
# ---------------------------------------------------------------------------

def _get_bus():
    from agent.ui.events import BUS
    return BUS


def _get_session():
    from agent.session import get_session
    return get_session()


def _run_agent_fn(user_input: str, extra_system: str | None = None):
    from agent.agent_loop import run_agent
    return run_agent(user_input, extra_system)


def _getenv(key: str, default: str = "") -> str:
    from agent.env import getenv
    return getenv(key) or default


def _setenv(key: str, value: str) -> None:
    from agent.env import setenv
    setenv(key, value)


# ---------------------------------------------------------------------------
# Known config keys exposed to the frontend
# ---------------------------------------------------------------------------

_CONFIG_KEYS = [
    "KRYTH_BASE_URL",
    "KRYTH_MAIN_MODEL",
    "KRYTH_PLANNER_MODEL",
    "KRYTH_FAST_MODEL",
    "KRYTH_PROFILE",
    "KRYTH_EXEC_PROFILE",
    "KRYTH_NO_PERSIST",
    "KRYTH_ASSUME_YES",
    "KRYTH_READ_TIMEOUT",
    "KRYTH_LOG_DIR",
]

# ---------------------------------------------------------------------------
# WebSocket client registry
# ---------------------------------------------------------------------------

_ws_clients: set[WebSocket] = set()
_ws_lock = threading.Lock()


async def _broadcast(payload: dict) -> None:
    """Send JSON to all connected event-stream clients."""
    text = json.dumps(payload)
    dead: list[WebSocket] = []
    with _ws_lock:
        clients = list(_ws_clients)
    for ws in clients:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    with _ws_lock:
        for ws in dead:
            _ws_clients.discard(ws)


# Main-thread event loop reference (set at startup)
_loop: asyncio.AbstractEventLoop | None = None


def _broadcast_sync(payload: dict) -> None:
    """Thread-safe broadcast from the agent thread."""
    if _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_broadcast(payload), _loop)


# ---------------------------------------------------------------------------
# Event Bus bridge — subscribe once at startup
# ---------------------------------------------------------------------------

import re

_TOOL_CALL_RE = re.compile(
    r"</?tool_call>|</?function[^>]*>|</?parameter[^>]*>|"
    r"<function=|<parameter=",
    re.IGNORECASE,
)

# Plain-text tool call patterns: write_file path="..." or write_file(path="...")
_PLAIN_TOOL_CALL_RE = re.compile(
    r"(?:write_file|read_file|run_command|create_file|delete_file|list_files|"
    r"search_code|grep_search|web_search|todo_write|git_commit|git_diff|"
    r"npm_install|pip_install|docker_run|http_request|subagent)"
    r"(?:\s*\(|\s+)(?:path|command|content|query|pattern|url|items)\s*=",
    re.IGNORECASE,
)

# Detect leaked internal reasoning (chain-of-thought)
_REASONING_RE = re.compile(
    r"^(?:Maybe|Perhaps|Let me|I think|Given the|But (?:the|I|that|again)|"
    r"Another|Could be|That (?:seems|would|could)|Unless|"
    r"Alternatively|However|Thus|Therefore|I'll|I could|I need to|"
    r"I should|I don't|I can|The user|The instruction|Maybe the user)",
    re.MULTILINE,
)


def _is_tool_call_fragment(piece: str) -> bool:
    """Return True if a streaming chunk contains raw tool-call content that should be hidden."""
    stripped = piece.strip()
    if not stripped:
        return False
    # XML-style tool calls
    if _TOOL_CALL_RE.search(stripped):
        return True
    # Plain-text tool calls like: write_file path="test" content="..."
    if _PLAIN_TOOL_CALL_RE.search(stripped):
        return True
    # Leaked internal reasoning: long chunks with many reasoning-style sentence starters
    if len(stripped) > 200:
        matches = _REASONING_RE.findall(stripped)
        if len(matches) >= 3:
            return True
    return False


def _install_bus_bridge() -> None:
    """Subscribe to KRYTH event bus and forward filtered events over WebSocket."""
    bus = _get_bus()

    def _on_event(event) -> None:
        kind = event.kind.value if hasattr(event.kind, "value") else str(event.kind)
        data = event.data or {}

        # Filter out raw tool-call XML in streaming content chunks
        if kind == "llm.content.chunk":
            piece = data.get("piece", "")
            if _is_tool_call_fragment(piece):
                return

        _broadcast_sync({
            "kind": kind,
            "id": event.id,
            "ts": event.ts,
            "data": data,
        })

    bus.subscribe(_on_event)


# ---------------------------------------------------------------------------
# Approval system (monkey-patch io.confirm)
# ---------------------------------------------------------------------------

# Map of approval_id → threading.Event + result placeholder
_pending_approvals: dict[str, dict] = {}
_approval_lock = threading.Lock()


def _install_approval_intercept() -> None:
    """Monkey-patch the agent's permission system to route approvals through
    the desktop UI instead of the CLI's interactive Rich prompt.

    Patches both:
    - agent.io.confirm (simple yes/no)
    - agent.permissions.ask_user (tool approval with y/a/n)
    """
    import agent.io as _io
    import agent.permissions as _perms

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

        high_risk_words = ("delete", "remove", "drop", "format", "truncate",
                           "system32", "rm -rf", "rmdir", "force")
        risk = "high" if any(w in message.lower() for w in high_risk_words) else "medium"

        _broadcast_sync({
            "kind": "approval_request",
            "id": aid,
            "ts": 0.0,
            "data": {
                "message": message,
                "risk": risk,
                "default": default,
            },
        })

        granted = evt.wait(timeout=120)

        with _approval_lock:
            _pending_approvals.pop(aid, None)

        return result["approved"] if granted else default

    def _desktop_ask_user(tool: str, args: dict) -> str:
        """Route tool permission requests through the desktop approval UI.

        Returns 'allow' or 'deny' (desktop doesn't support 'always' since
        there's no session-level memory visible to the user yet).
        """
        try:
            from agent.env import getenv_bool
            if getenv_bool("KRYTH_ASSUME_YES"):
                return "allow"
        except Exception:
            pass

        # Build a human-readable description of what the tool wants to do
        sig = _perms._args_signature(tool, args)
        message = f"{tool}: {sig}"

        aid = str(uuid.uuid4())
        evt = threading.Event()
        result: dict = {"approved": True}

        with _approval_lock:
            _pending_approvals[aid] = {"event": evt, "result": result}

        # Determine risk
        high_risk_words = ("delete", "remove", "drop", "format", "truncate",
                           "system32", "rm -rf", "rmdir", "force", "sudo")
        combined = f"{tool} {sig}".lower()
        risk = "high" if any(w in combined for w in high_risk_words) else "medium"

        _broadcast_sync({
            "kind": "approval_request",
            "id": aid,
            "ts": 0.0,
            "data": {
                "message": message,
                "risk": risk,
                "tool": tool,
                "default": True,
            },
        })

        granted = evt.wait(timeout=120)

        with _approval_lock:
            _pending_approvals.pop(aid, None)

        if not granted:
            return "deny"
        return "allow" if result["approved"] else "deny"

    _io.confirm = _desktop_confirm
    _perms.ask_user = _desktop_ask_user


# ---------------------------------------------------------------------------
# Agent execution state
# ---------------------------------------------------------------------------

_agent_lock = threading.Lock()
_agent_running = False


def _run_agent_thread(user_input: str, cwd: str) -> None:
    global _agent_running
    try:
        if cwd and os.path.isdir(cwd):
            os.chdir(cwd)
        _run_agent_fn(user_input)
    finally:
        _agent_running = False
        _broadcast_sync({"kind": "agent.idle", "id": str(uuid.uuid4()), "ts": 0.0, "data": {}})


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    user_input: str
    cwd: str = ""


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
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="KRYTH Desktop Bridge", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _on_startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    _install_bus_bridge()
    _install_approval_intercept()


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"ok": True, "version": "1.0.0"}


@app.post("/api/agent/run")
async def agent_run(req: RunRequest):
    global _agent_running
    with _agent_lock:
        if _agent_running:
            raise HTTPException(status_code=409, detail="Agent already running")
        _agent_running = True

    t = threading.Thread(
        target=_run_agent_thread,
        args=(req.user_input, req.cwd),
        daemon=True,
    )
    t.start()
    return {"ok": True, "message": "Agent started"}


@app.post("/api/agent/stop")
async def agent_stop():
    try:
        session = _get_session()
        session._task_interrupted = True
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/approve")
async def approve(req: ApproveRequest):
    with _approval_lock:
        entry = _pending_approvals.get(req.id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Approval request not found")
    entry["result"]["approved"] = req.approved
    entry["event"].set()
    return {"ok": True}


@app.get("/api/config")
async def config_get():
    result: dict[str, str] = {}
    for key in _CONFIG_KEYS:
        result[key] = _getenv(key)
    return result


@app.patch("/api/config")
async def config_patch(req: ConfigPatchRequest):
    if req.key not in _CONFIG_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown config key: {req.key}")
    _setenv(req.key, req.value)
    return {"ok": True, "key": req.key, "value": req.value}


@app.get("/api/files")
async def list_files(path: str = "."):
    target = Path(path).resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")

    entries = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            entries.append({
                "name": entry.name,
                "path": str(entry),
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {"path": str(target), "entries": entries}


@app.get("/api/file")
async def read_file(path: str):
    target = Path(path).resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {"path": str(target), "content": content}


@app.post("/api/file")
async def write_file(req: FileWriteRequest):
    target = Path(req.path).resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(req.content, encoding="utf-8")
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    return {"ok": True, "path": str(target)}


@app.get("/api/tools")
async def list_tools():
    """Return registered tools from the agent runtime."""
    tools = []
    try:
        from agent.tools import get_all_tools
        for tool in get_all_tools():
            tools.append({
                "name": tool.name if hasattr(tool, "name") else str(tool),
                "description": tool.description if hasattr(tool, "description") else "",
                "source": "mcp" if hasattr(tool, "mcp_server") else "builtin",
            })
    except Exception:
        # Fallback: list common built-in tool names
        _builtin_names = [
            ("read_file", "Read a file from the filesystem"),
            ("write_file", "Write content to a file"),
            ("edit_file", "Edit a file with search/replace"),
            ("run_command", "Run a shell command"),
            ("search_files", "Search for files by name pattern"),
            ("grep", "Search file contents with regex"),
            ("list_directory", "List directory contents"),
            ("web_search", "Search the web"),
        ]
        tools = [{"name": n, "description": d, "source": "builtin"} for n, d in _builtin_names]
    return {"tools": tools}


@app.get("/api/sessions")
async def list_sessions():
    """Return recent sessions/conversations."""
    sessions = []
    try:
        from agent.session import list_sessions as _list_sessions
        for s in _list_sessions():
            sessions.append({
                "id": s.id if hasattr(s, "id") else str(s),
                "project_path": s.project_path if hasattr(s, "project_path") else "",
                "updated_at": s.updated_at if hasattr(s, "updated_at") else "",
            })
    except Exception:
        # No session backend available — return empty
        pass
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}/history")
async def get_session_history(session_id: str):
    """Return the event history for a given session."""
    events = []
    try:
        from agent.session import get_session_history as _get_history
        raw = _get_history(session_id)
        if raw:
            events = raw
    except Exception:
        # If session history is not available, return empty
        pass
    return {"session_id": session_id, "events": events}


@app.get("/api/memory")
async def get_memory():
    """Return context/memory entries the agent is using."""
    entries = []
    try:
        from agent.memory import get_memory_entries
        for entry in get_memory_entries():
            entries.append({
                "id": entry.id if hasattr(entry, "id") else str(hash(entry)),
                "content": entry.content if hasattr(entry, "content") else str(entry),
                "source": entry.source if hasattr(entry, "source") else "system",
                "ts": entry.ts if hasattr(entry, "ts") else "",
            })
    except Exception:
        # Memory module may not exist — return empty
        pass
    return {"entries": entries}


@app.delete("/api/memory/{entry_id}")
async def delete_memory(entry_id: str):
    """Delete a memory entry."""
    try:
        from agent.memory import delete_memory_entry
        delete_memory_entry(entry_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Memory entry not found or not deletable")
    return {"ok": True}


@app.get("/api/logs")
async def get_logs(limit: int = 100):
    """Return recent agent log entries."""
    lines = []
    try:
        log_dir = _getenv("KRYTH_LOG_DIR", "")
        if log_dir:
            log_path = Path(log_dir) / "agent.log"
            if log_path.exists():
                all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                lines = all_lines[-limit:]
    except Exception:
        pass
    return {"lines": lines}


# ---------------------------------------------------------------------------
# WebSocket: event stream
# ---------------------------------------------------------------------------

@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    with _ws_lock:
        _ws_clients.add(websocket)
    # Send connection ack
    await websocket.send_text(json.dumps({
        "kind": "connection.ready",
        "id": str(uuid.uuid4()),
        "ts": 0.0,
        "data": {"version": "1.0.0"},
    }))
    try:
        while True:
            # Keep connection alive; handle any incoming control messages
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                # Frontend can send {"kind": "ping"} for keepalive
                data = json.loads(msg)
                if data.get("kind") == "ping":
                    await websocket.send_text(json.dumps({"kind": "pong"}))
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_text(json.dumps({"kind": "ping"}))
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            _ws_clients.discard(websocket)


# ---------------------------------------------------------------------------
# WebSocket: shell pty
# ---------------------------------------------------------------------------

@app.websocket("/ws/shell")
async def ws_shell(websocket: WebSocket):
    await websocket.accept()
    try:
        import ptyprocess
        _pty_available = True
    except ImportError:
        _pty_available = False

    if not _pty_available:
        # Fallback: simple subprocess pipe (no pty, limited interactivity)
        await _ws_shell_subprocess(websocket)
        return

    await _ws_shell_pty(websocket)


async def _ws_shell_subprocess(websocket: WebSocket) -> None:
    """Fallback shell over subprocess (no pty — Windows default)."""
    import subprocess
    shell = os.environ.get("COMSPEC", "cmd.exe") if sys.platform == "win32" else "/bin/bash"

    proc = await asyncio.create_subprocess_shell(
        shell,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def _read_output():
        assert proc.stdout
        try:
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)
        except Exception:
            pass

    asyncio.create_task(_read_output())

    try:
        while True:
            data = await websocket.receive_bytes()
            if proc.stdin:
                proc.stdin.write(data)
                await proc.stdin.drain()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            proc.kill()
        except Exception:
            pass


async def _ws_shell_pty(websocket: WebSocket) -> None:
    """Full pty shell (Unix/ptyprocess)."""
    import ptyprocess
    shell = os.environ.get("SHELL", "/bin/bash")
    proc = ptyprocess.PtyProcess.spawn([shell])

    async def _read():
        loop = asyncio.get_running_loop()
        try:
            while proc.isalive():
                chunk = await loop.run_in_executor(None, proc.read, 4096)
                if chunk:
                    await websocket.send_bytes(chunk)
        except Exception:
            pass

    asyncio.create_task(_read())

    try:
        while True:
            data = await websocket.receive_bytes()
            proc.write(data)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
