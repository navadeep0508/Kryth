"""BrowserManager: locates, starts, and manages the KRYTH Browser relay lifecycle.

The Rust relay binary (vendored from anomalyco/browse) is pre-built with
``cargo build --release`` and lives at
``kryth_browser/vendor/rust/target/release/browse.exe`` (or ``browse`` on
Unix).  The manager probes for an already-running relay, starts one on demand,
and hands out :class:`RelayClient` instances to the browser tools.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

from config.config import Config

from .relay_client import (
    DEFAULT_PORT,
    FALLBACK_PORTS,
    RelayClient,
    read_relay_state,
    read_relay_token,
)

logger = logging.getLogger(__name__)

RELAY_BINARY_NAME = "browse.exe" if os.name == "nt" else "browse"


class BrowserManager:
    """Owns the KRYTH Browser relay subprocess for one KRYTH session."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._client: RelayClient | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._started_by_us = False

    # ------------------------------------------------------------------ binary

    def relay_binary_path(self) -> Path | None:
        """Return the path to a pre-built browse relay binary, if any."""
        vendor_dir = Path(__file__).resolve().parent.parent / "vendor" / "rust"
        candidates = [
            vendor_dir / "target" / "release" / RELAY_BINARY_NAME,
            vendor_dir / "target" / "release" / "browse",
        ]
        for cand in candidates:
            if cand.is_file():
                return cand
        return None

    # ------------------------------------------------------------------ probing

    @staticmethod
    def probe(port: int = DEFAULT_PORT, timeout: float = 2.0) -> bool:
        """Return True when a browse relay answers /version on *port*."""
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/version")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    import json

                    data = json.loads(resp.read().decode("utf-8"))
                    return isinstance(data, dict) and bool(data.get("version"))
        except Exception:  # noqa: BLE001 — any failure means "not reachable"
            pass
        return False

    def find_running_client(self) -> RelayClient | None:
        """Find an already-running relay and return a client for it."""
        token = read_relay_token()
        if not token:
            return None
        for port in [DEFAULT_PORT] + FALLBACK_PORTS:
            state = read_relay_state(port)
            if state and state.get("instance_id"):
                # A relay state file exists for this port; probe to confirm.
                if self.probe(port):
                    return RelayClient(port, token)
            elif self.probe(port):
                return RelayClient(port, token)
        return None

    # ------------------------------------------------------------------ start / stop

    async def start_relay(self) -> bool:
        """Start the relay binary if none is running, then return a client."""
        existing = self.find_running_client()
        if existing is not None:
            self._client = existing
            return True

        binary = self.relay_binary_path()
        if binary is None:
            logger.error(
                "KRYTH Browser relay binary not found at vendor/rust/target/release/%s. "
                "Build it with `cargo build --release` in vendor/rust/.",
                RELAY_BINARY_NAME,
            )
            return False

        port = DEFAULT_PORT
        env = os.environ.copy()
        env["OPENCODE_BROWSER_PORT"] = str(port)
        env["OPENCODE_BROWSER_AUTOSTART"] = "1"

        try:
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            )
            self._process = subprocess.Popen(
                [str(binary), "serve"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._started_by_us = True
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to start KRYTH Browser relay: %s", exc)
            return False

        # Wait up to ~10 s for the relay to answer /version.
        for _ in range(100):
            await asyncio.sleep(0.1)
            if self.probe(port):
                token = read_relay_token()
                if token:
                    self._client = RelayClient(port, token)
                    return True

        logger.error("KRYTH Browser relay did not start within 10 seconds")
        return False

    async def stop_relay(self) -> None:
        """Stop the relay if we started it."""
        if self._client is not None:
            try:
                await self._client.shutdown()
            except Exception:  # noqa: BLE001
                logger.debug("relay shutdown request failed", exc_info=True)
            self._client = None
        if self._process is not None and self._started_by_us:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self._process.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._process = None

    # ------------------------------------------------------------------ client access

    def get_client(self) -> RelayClient | None:
        """Return a connected RelayClient, starting the relay if possible.

        This is synchronous: it only returns a client for an already-running
        relay.  Use :meth:`start_relay` (async) to start one from an async
        context.
        """
        if self._client is not None:
            return self._client
        return self.find_running_client()

    # ------------------------------------------------------------------ discovery

    def discover(self) -> list:
        """Return the KRYTH Browser tools, if browser integration is enabled.

        Tools connect to the relay lazily on first use; the relay is started
        then.  Returns an empty list when browser integration is disabled or
        excluded by ``allowed_tools``.
        """
        from .tool import KRYTHBrowserTool
        from .connect import BrowserConnectTool
        from .disconnect import BrowserDisconnectTool
        from .status import BrowserStatusTool

        browser_cfg = getattr(self.config, "kryth_browser", None)
        if browser_cfg is None or not getattr(browser_cfg, "enabled", False):
            return []

        if self.config.allowed_tools:
            browser_tool_names = {
                "browser",
                "browser_connect",
                "browser_disconnect",
                "browser_status",
            }
            if not browser_tool_names.intersection(self.config.allowed_tools):
                return []

        return [
            KRYTHBrowserTool(self.config, self),
            BrowserConnectTool(self.config, self),
            BrowserDisconnectTool(self.config, self),
            BrowserStatusTool(self.config, self),
        ]

    async def shutdown(self) -> None:
        """Alias for stop_relay, kept for compatibility with session shutdown."""
        await self.stop_relay()
