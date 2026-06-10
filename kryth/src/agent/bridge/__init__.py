"""XerocodeAI Local Provider Bridge.

Starts a local FastAPI server that:
  - Authenticates to Gemini / Claude / OpenAI via a real browser (Playwright)
  - Persists browser sessions so you only log in once
  - Exposes an OpenAI-compatible REST + WebSocket API on localhost

Usage (from REPL):
    /bridge start [--port 8765] [--provider gemini|claude|openai]
    /bridge stop
    /bridge status
    /bridge auth <provider>   # re-run browser login for a provider

Or directly:
    python -m agent.bridge --port 8765 --provider gemini

Environment variables set automatically when bridge starts:
    KRYTH_BASE_URL  → http://localhost:<port>/v1
    KRYTH_MAIN_MODEL → <provider-default-model>
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from agent.env import getenv, setenv

# Default port — overridable via KRYTH_BRIDGE_PORT
try:
    DEFAULT_PORT = int(getenv("XEROCODEAI_BRIDGE_PORT", "8765"))
except ValueError:
    DEFAULT_PORT = 8765

# Where browser session profiles are stored (never committed)
SESSION_DIR = Path.home() / ".kryth" / "browser_sessions"

# Track the running server process
_server_proc: Optional[subprocess.Popen] = None
_server_thread: Optional[threading.Thread] = None


def start(port: int = DEFAULT_PORT, provider: str = "gemini") -> None:
    """Start the bridge server in a background thread."""
    global _server_proc

    if is_running():
        print(f"  bridge already running on port {port}")
        return

    SESSION_DIR.mkdir(parents=True, exist_ok=True)

    # Launch as a subprocess so it survives REPL restarts
    cmd = [
        sys.executable, "-m", "agent.bridge.server",
        "--port", str(port),
        "--provider", provider,
    ]
    _server_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    # Set env so llm.py picks up the bridge automatically
    setenv("XEROCODEAI_BASE_URL", f"http://localhost:{port}/v1")
    os.environ["OPENAI_API_KEY"] = "bridge-local"

    # Wait briefly for the server to be ready
    _wait_ready(port, timeout=8.0)


def stop() -> None:
    """Stop the running bridge server."""
    global _server_proc
    if _server_proc and _server_proc.poll() is None:
        _server_proc.terminate()
        try:
            _server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _server_proc.kill()
    _server_proc = None


def is_running() -> bool:
    return _server_proc is not None and _server_proc.poll() is None


def status() -> dict:
    return {
        "running": is_running(),
        "pid": _server_proc.pid if is_running() else None,
        "base_url": getenv("XEROCODEAI_BASE_URL"),
    }


def _wait_ready(port: int, timeout: float = 8.0) -> bool:
    """Poll until the server responds on /health or timeout."""
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(
                f"http://localhost:{port}/health", timeout=1
            )
            return True
        except Exception:
            time.sleep(0.3)
    return False
