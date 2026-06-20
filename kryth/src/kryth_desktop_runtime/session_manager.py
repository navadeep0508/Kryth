"""Session Manager — desktop session lifecycle over KRYTH sessions.

Wraps the KRYTH session with desktop-specific metadata (project path,
SQLite persistence, model tracking) while keeping the KRYTH Session
object as the authoritative runtime state.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from kryth_desktop_runtime import persistence as db

_lock = threading.Lock()
_current_session_id: Optional[str] = None
_current_project_path: str = ""
_agent_running = False


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
    """Execute run_agent() in the current session context."""
    global _agent_running

    with _lock:
        if _agent_running:
            raise RuntimeError("Agent already running")
        _agent_running = True

    try:
        if cwd and os.path.isdir(cwd):
            os.chdir(cwd)

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
    try:
        from agent.session import get_session
        sess = get_session()
        sess._task_interrupted = True
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
