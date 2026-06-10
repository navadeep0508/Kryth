"""Session manager for OpenCLI browser sessions backed by Chrome profiles.

OpenCLI sessions map 1-to-1 with Chrome profile aliases managed via:
    opencli profile list
    opencli profile use <name>
    opencli browser <name> init
    opencli browser <name> close
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except FileNotFoundError:
        return 1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 1, "", "timed out"
    except Exception as e:
        return 1, "", str(e)


def _cli() -> str:
    cli = shutil.which("opencli") or shutil.which("opencli.cmd")
    if not cli:
        raise RuntimeError("opencli not found — run: npm install -g @jackwener/opencli")
    return cli


@dataclass
class SessionState:
    session_id: str
    profile: str
    active_tab: Optional[str] = None


class SessionManager:
    """Manages named OpenCLI browser sessions backed by Chrome profile aliases."""

    def __init__(self) -> None:
        self._active: dict[str, SessionState] = {}

    def create_session(self, name: str = "default", profile: Optional[str] = None) -> SessionState:
        """Initialize a new browser session for the given profile name."""
        profile_name = profile or name
        cli = _cli()
        _run([cli, "browser", profile_name, "init"], timeout=30)
        state = SessionState(session_id=profile_name, profile=profile_name)
        self._active[profile_name] = state
        return state

    def restore_session(self, name: str) -> SessionState:
        """Return an existing active session or resolve the real profile name.

        If ``name`` is "default" (or any alias that doesn't exist), auto-discovers
        the first connected Chrome profile from ``opencli profile list``.
        """
        if name in self._active:
            return self._active[name]

        cli = _cli()

        # Try the name as-is first
        rc, _, _ = _run([cli, "browser", name, "state"], timeout=5)
        if rc == 0:
            state = SessionState(session_id=name, profile=name)
            self._active[name] = state
            return state

        # Auto-discover the real profile
        from agent.providers.opencli.browser import _discover_session
        discovered = _discover_session()
        resolved = discovered or name

        if resolved != name:
            # Also register under the original requested name for lookup
            self._active[name] = SessionState(session_id=resolved, profile=resolved)

        state = SessionState(session_id=resolved, profile=resolved)
        self._active[resolved] = state
        return state

    def list_sessions(self) -> list[dict]:
        """List all available Chrome profiles known to opencli."""
        try:
            cli = _cli()
        except RuntimeError:
            return []
        rc, out, err = _run([cli, "profile", "list", "-f", "json"], timeout=15)
        if rc != 0:
            return []
        try:
            data = json.loads(out)
            if isinstance(data, list):
                return data
            return [data]
        except (json.JSONDecodeError, ValueError):
            return [{"name": ln.strip()} for ln in out.splitlines() if ln.strip()]

    def close_session(self, name: str) -> str:
        """Close the named browser session."""
        try:
            cli = _cli()
        except RuntimeError as e:
            return f"[ERROR] {e}"
        rc, out, err = _run([cli, "browser", name, "close"], timeout=15)
        self._active.pop(name, None)
        if rc not in (0, 69):  # 69 = browser bridge already down
            return f"[ERROR {rc}] {err or out}"
        return f"session '{name}' closed"

    def get_active(self, name: str) -> Optional[SessionState]:
        return self._active.get(name)


# Module-level singleton — shared across the provider
_manager: Optional[SessionManager] = None


def get_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
