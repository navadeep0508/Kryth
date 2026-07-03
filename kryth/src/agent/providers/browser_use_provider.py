"""
Browser-use provider — integration layer between Kryth and browser_agent.

Recreated after stabilization cleanup. Wraps the existing
``KrythBrowserBridge`` in ``agent/browser-use/kryth_browser_bridge.py``
and provides the stable API surface that the browser tools expect.

Exports
-------
browser_task          — AI-driven multi-step browser automation
ensure_available      — check if browser-use is usable, return error or None
_run                  — run an async coroutine synchronously (blocking)
_ensure_path          — ensure Playwright browsers are installed
_get_worker           — get the shared persistent worker (for desktop)
reset_browser_interrupt — clear the interrupt flag
force_stop_browser    — force-stop the browser worker
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Lazy bridge loader
# ---------------------------------------------------------------------------

_BRIDGE = None  # Lazy-loaded KrythBrowserBridge


def _get_bridge() -> Any:
    global _BRIDGE
    if _BRIDGE is not None:
        return _BRIDGE
    _bridge_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "agent", "browser-use",
    )
    _bridge_path = os.path.normpath(os.path.abspath(_bridge_path))
    if _bridge_path not in sys.path:
        sys.path.insert(0, _bridge_path)
    from kryth_browser_bridge import KrythBrowserBridge  # type: ignore[import-untyped]
    _BRIDGE = KrythBrowserBridge
    return _BRIDGE


# ---------------------------------------------------------------------------
# Tool API — used by agent/tools/_opencli.py
# ---------------------------------------------------------------------------

def browser_task(
    task: str,
    llm_provider: str = "auto",
    model_name: str | None = None,
    max_steps: int = 10,
    headless: bool = False,
    use_vision: bool = True,
) -> str:
    """Execute a complete browser automation task using the AI-driven agent.

    Parameters
    ----------
    task:
        Natural-language description of the task.
    llm_provider:
        ``"auto"`` (detect from env), ``"nvidia"``, ``"openai"``,
        ``"anthropic"``, ``"google"``, ``"ollama"``.
    model_name:
        Override the default model. Falls back to
        ``KRYTH_BROWSER_MODEL`` env var, then a provider default.
    max_steps:
        Maximum agent steps (default 10).
    headless:
        Run without a visible window (default **False** — shows browser).
    use_vision:
        Enable vision/screenshots (default True).

    Returns
    -------
    str
        A human-readable result or an error message prefixed with ``[ERROR]``.
    """
    try:
        bridge_cls = _get_bridge()
        resolved_provider, resolved_model = _resolve_provider(
            llm_provider, model_name,
        )

        bridge = bridge_cls(
            llm_provider=resolved_provider,
            model_name=resolved_model,
            headless=headless,
            max_steps=max_steps,
            use_vision=use_vision,
        )
        result = bridge.run_sync(task)

        if result.get("success"):
            final = result.get("final_result", "")
            steps = result.get("steps", 0)
            return f"Task completed in {steps} step(s).\n{final}"
        else:
            error = result.get("error", "Unknown error")
            return f"[ERROR] browser_task failed: {error}"
    except ImportError as e:
        return (
            f"[ERROR] browser agent not available. "
            f"Install with: pip install browser-use playwright && "
            f"playwright install chromium\n  ({e})"
        )
    except Exception as e:
        return f"[ERROR] browser_task failed: {e}"


# ---------------------------------------------------------------------------
# Infrastructure API — used by agent/tools/_browser.py
# ---------------------------------------------------------------------------

def ensure_available() -> str | None:
    """Check if the browser automation stack is available.

    Returns an error-message string if unavailable, or ``None`` on success.
    """
    # 1) Can we import the bridge?
    try:
        _get_bridge()
    except ImportError as e:
        return (
            f"browser-use stack not available: {e}. "
            f"Install: pip install browser-use playwright && "
            f"playwright install chromium"
        )

    # 2) Is playwright installed?
    try:
        import playwright  # noqa: F401
    except ImportError:
        return "Playwright not installed. Run: pip install playwright"

    # 3) Are browser binaries available?
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            return (
                "Chromium browser not found. Run: python -m playwright install chromium"
            )
    except Exception:
        pass  # dry-run may not be available; proceed optimistically

    return None


def _run(coro: Any, timeout: float = 30.0) -> str | None:
    """Run an async coroutine synchronously.

    Creates a fresh event loop, runs *coro* with *timeout*,
    then returns ``None`` on success or an error string on failure.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
        return None
    except asyncio.TimeoutError:
        return f"[ERROR] Browser operation timed out after {timeout}s"
    except Exception as e:
        return f"[ERROR] Browser operation failed: {e}"
    finally:
        try:
            loop.close()
        except Exception:
            pass


def _ensure_path() -> None:
    """Ensure Playwright browsers are installed on disk.

    No-op if already available.  Raises ``RuntimeError`` on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Chromium is detected — all good
            return
    except Exception:
        pass

    # Install chromium
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True, text=True, timeout=120,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to install Chromium: {e.stderr or e.stdout or e}"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(
            "Playwright CLI not found. Run: pip install playwright"
        ) from e


# ---------------------------------------------------------------------------
# Desktop worker API — used by kryth_desktop_runtime/*
# ---------------------------------------------------------------------------

_WORKER: Any = None
_WORKER_LOCK = threading.Lock()
_BROWSER_INTERRUPT = threading.Event()


def _get_worker() -> Any:
    """Get or create a shared persistent browser worker for the desktop app.

    Returns the worker instance (opaque to callers).
    """
    global _WORKER
    if _WORKER is not None:
        _BROWSER_INTERRUPT.clear()
        return _WORKER

    with _WORKER_LOCK:
        if _WORKER is not None:
            _BROWSER_INTERRUPT.clear()
            return _WORKER

        # Lazy-import the desktop bridge
        try:
            from kryth_desktop_runtime.browser_bridge import BrowserWorker
        except ImportError:
            # Fallback: build a minimal worker from the bridge
            bridge_cls = _get_bridge()
            _WORKER = bridge_cls(headless=False, max_steps=30)
            _BROWSER_INTERRUPT.clear()
            return _WORKER

        _WORKER = BrowserWorker()
        _BROWSER_INTERRUPT.clear()
        return _WORKER


def reset_browser_interrupt() -> None:
    """Clear the browser interrupt flag."""
    _BROWSER_INTERRUPT.clear()


def force_stop_browser() -> None:
    """Signal the browser worker to stop immediately."""
    _BROWSER_INTERRUPT.set()

    # If we have a worker with a stop method, call it
    global _WORKER
    if _WORKER is not None:
        try:
            if hasattr(_WORKER, "stop"):
                _WORKER.stop()
            elif hasattr(_WORKER, "close"):
                _WORKER.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROVIDER_ENV_MAP: dict[str, dict[str, str]] = {
    "nvidia": {"env_key": "NVIDIA_API_KEY", "default_model": "stepfun-ai/step-3.7-flash"},
    "openai": {"env_key": "OPENAI_API_KEY", "default_model": "gpt-4o"},
    "anthropic": {"env_key": "ANTHROPIC_API_KEY", "default_model": "claude-sonnet-4-20250514"},
    "google": {"env_key": "GOOGLE_API_KEY", "default_model": "gemini-2.0-flash"},
    "ollama": {"env_key": None, "default_model": "llama3.2"},
}


def _resolve_provider(
    provider: str, model: str | None,
) -> tuple[str, str]:
    """Resolve ``"auto"`` to a concrete provider+model based on env vars."""
    if provider != "auto":
        resolved = provider
        resolved_model = (
            model
            or os.getenv("KRYTH_BROWSER_MODEL")
            or _PROVIDER_ENV_MAP.get(provider, {}).get("default_model", "gpt-4o")
        )
        return resolved, resolved_model

    # Auto-detect: check which API keys are set
    for prov, info in _PROVIDER_ENV_MAP.items():
        if info["env_key"] and os.getenv(info["env_key"]):
            resolved_model = (
                model
                or os.getenv("KRYTH_BROWSER_MODEL")
                or info["default_model"]
            )
            return prov, resolved_model

    # Fallback: NVIDIA
    fallback_model = model or os.getenv("KRYTH_BROWSER_MODEL", "stepfun-ai/step-3.7-flash")
    return "nvidia", fallback_model
