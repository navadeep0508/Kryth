"""Session Manager — desktop session lifecycle over KRYTH sessions.

Wraps the KRYTH session with desktop-specific metadata (project path,
SQLite persistence, model tracking) while keeping the KRYTH Session
object as the authoritative runtime state.
"""

from __future__ import annotations

import os
import time
import threading
from typing import Optional

from kryth_desktop_runtime import persistence as db

_lock = threading.Lock()
_current_session_id: Optional[str] = None
_current_project_path: str = ""
_agent_running = False
_graphify_initialized: set = set()  # track which dirs have been initialized


def _auto_init_graphify(cwd: str) -> None:
    """Auto-build Graphify knowledge graph for the project in background.
    
    Runs AST extraction (no API calls, fully local) in a daemon thread.
    Only runs once per directory per session.
    """
    if cwd in _graphify_initialized:
        return
    _graphify_initialized.add(cwd)

    def _build():
        try:
            import subprocess
            import shutil
            graphify_bin = shutil.which("graphify")
            if not graphify_bin:
                return
            # Check if graph already exists and is recent (< 1 hour old)
            graph_file = os.path.join(cwd, "graphify-out", "graph.json")
            if os.path.exists(graph_file):
                age = time.time() - os.path.getmtime(graph_file)
                if age < 3600:  # less than 1 hour old — skip rebuild
                    return
            # Run graphify extract (AST only, no LLM, no API cost)
            subprocess.run(
                [graphify_bin, "extract", cwd, "--no-cluster"],
                cwd=cwd,
                timeout=120,
                capture_output=True,
            )
        except Exception:
            pass

    t = threading.Thread(target=_build, daemon=True, name="kryth-graphify-init")
    t.start()


def current_session_id() -> Optional[str]:
    return _current_session_id


def is_running() -> bool:
    return _agent_running


def start_session(project_path: str = "", model: str = "") -> str:
    global _current_session_id, _current_project_path

    if not model:
        try:
            from agent.env import getenv
            model = getenv("KRYTH_MAIN_MODEL") or "unknown"
        except Exception:
            model = "unknown"

    sid = db.create_session(project_path=project_path, model=model)
    _current_session_id = sid
    _current_project_path = project_path

    if project_path and os.path.isdir(project_path):
        db.upsert_project(project_path)

    return sid


def persist_user_message(content: str) -> str:
    sid = _current_session_id or start_session()
    return db.save_message(sid, "user", content)


def persist_assistant_message(content: str, tool_actions: list) -> str:
    sid = _current_session_id or start_session()
    msg_id = db.save_message(sid, "assistant", content, tool_actions)
    db.touch_session(sid)
    return msg_id


def set_running(running: bool) -> None:
    global _agent_running
    _agent_running = running


def run_agent(user_input: str, cwd: str = "") -> None:
    """Execute run_agent() in the current session context.
    
    IMPORTANT: The agent MUST run in the user's opened project folder.
    If no cwd is provided, uses _current_project_path from the session.
    The agent is NEVER allowed to run in the kryth source directory.
    """
    global _agent_running, _current_project_path

    with _lock:
        if _agent_running:
            raise RuntimeError("Agent already running")
        _agent_running = True

    try:
        # Reset browser interrupt flag from previous stop
        try:
            from agent.providers.browser_use_provider import reset_browser_interrupt
            reset_browser_interrupt()
        except Exception:
            pass

        # Strictly enforce working directory — use provided cwd, fall back to session project
        effective_cwd = ""
        if cwd and os.path.isdir(cwd):
            effective_cwd = cwd
        elif _current_project_path and os.path.isdir(_current_project_path):
            effective_cwd = _current_project_path

        # ALWAYS chdir if we have a valid project path
        if effective_cwd:
            os.chdir(effective_cwd)
            _current_project_path = effective_cwd
            # Also set env var so tools that spawn subprocesses inherit it
            os.environ["KRYTH_PROJECT_DIR"] = effective_cwd
            # Auto-initialize Graphify knowledge graph in background
            _auto_init_graphify(effective_cwd)
        else:
            # No valid cwd — emit error and return
            from kryth_desktop_runtime import event_router
            event_router.broadcast_sync({
                "kind": "run.error",
                "id": "",
                "ts": 0.0,
                "data": {"error": "No project folder opened. Open a folder first."},
            })
            return

        # Reset interrupt flag from any previous stop
        try:
            from agent.session import get_session
            sess = get_session()
            sess._task_interrupted = False
            # Set permission profile from env (desktop default: 'auto')
            try:
                from agent.profiles import from_environment
                sess.profile = from_environment()
            except Exception:
                pass
        except Exception:
            pass

        persist_user_message(user_input)

        from agent.agent_loop import run_agent as _run
        result = _run(user_input)

        # Persist final assistant message from session history
        try:
            from agent.session import get_session
            sess = get_session()
            last = next(
                (m for m in reversed(sess.messages)
                 if m.get("role") == "assistant"), None
            )
            if last:
                content = ""
                if isinstance(last.get("content"), str):
                    content = last["content"]
                elif isinstance(last.get("content"), list):
                    content = " ".join(
                        p.get("text", "") for p in last["content"]
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                persist_assistant_message(content, [])
        except Exception:
            pass

    finally:
        _agent_running = False


def interrupt() -> None:
    """Stop everything — agent loop, browser tasks, background commands."""
    global _agent_running
    _agent_running = False

    # 1. Signal the agent session to stop
    try:
        from agent.session import get_session
        sess = get_session()
        sess._task_interrupted = True
    except Exception:
        pass

    # 2. Kill any running browser sessions
    try:
        from agent.providers.browser_use_provider import force_stop_browser
        force_stop_browser()
    except Exception:
        pass

    # 3. Kill background shell tasks
    try:
        from agent.tools._shell import BACKGROUND_TASKS
        for task_id, entry in list(BACKGROUND_TASKS.items()):
            proc = entry.get("proc")
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
    except Exception:
        pass

    # 4. Notify frontend gracefully
    try:
        from kryth_desktop_runtime import event_router
        event_router.broadcast_sync({
            "kind": "session_event",
            "id": "",
            "ts": 0.0,
            "data": {"event": "interrupted"},
        })
        event_router.broadcast_sync({
            "kind": "status_update",
            "id": "",
            "ts": 0.0,
            "data": {"status": "idle"},
        })
    except Exception:
        pass


def get_history(session_id: str | None = None) -> list[dict]:
    sid = session_id or _current_session_id
    if not sid:
        return []
    return db.get_messages(sid)


def get_recent_sessions() -> list[dict]:
    return db.get_sessions(limit=20)


def get_recent_projects() -> list[dict]:
    return db.get_projects(limit=10)
