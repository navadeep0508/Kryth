"""Async HTTP client for the KRYTH Browser relay (vendored anomalyco/browse).

The relay exposes a local HTTP API:

    GET  /version          — no auth; health/identity
    GET  /tools            — Bearer token; list tool specs
    POST /tools/call       — Bearer token; invoke a tool
    POST /tools/cancel     — Bearer token; cancel an in-flight call
    POST /shutdown         — Bearer token; stop the relay
    GET  /extension/status — Bearer token; extension pairing state

Every authorised request carries ``Authorization: Bearer <token>`` and an
``OpenCode-Browser-Client-Pid`` header.  The token lives at
``$XDG_DATA_HOME/browse/relay-token`` (``%LOCALAPPDATA%\\browse\\relay-token`` on
Windows) and the relay's chosen port/state lives at
``$XDG_STATE_HOME/browse/relays/<port>/relay.json``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PORT = 19988
FALLBACK_PORTS = [19990, 19991, 19992, 19993, 19994, 19995, 19996, 19997]

RELAY_TOKEN_ENV = "KRYTH_BROWSER_RELAY_TOKEN"
RELAY_PORT_ENV = "KRYTH_BROWSER_RELAY_PORT"


# ------------------------------------------------------------------- paths

def _home() -> Path:
    """User home directory, mirroring the Rust relay's home_dir()."""
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home())))
    return Path(os.environ.get("HOME", str(Path.home())))


def _browse_root() -> Path:
    """The browse data root, mirroring the Rust relay's path logic.

    On Windows (no XDG env vars): $USERPROFILE/.local/share/browse
    Otherwise: $XDG_DATA_HOME/browse or $HOME/.local/share/browse
    """
    if os.name == "nt":
        base = _home() / ".local" / "share"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(_home() / ".local" / "share")))
    return base / "browse"


def state_root() -> Path:
    """The browse state root, mirroring the Rust relay's path logic.

    On Windows (no XDG env vars): $USERPROFILE/.local/state/browse
    Otherwise: $XDG_STATE_HOME/browse or $HOME/.local/state/browse
    """
    if os.name == "nt":
        base = _home() / ".local" / "state"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", str(_home() / ".local" / "state")))
    return base / "browse"


def relay_token_path() -> Path:
    return _browse_root() / "relay-token"


def relay_state_path(port: int = DEFAULT_PORT) -> Path:
    return state_root() / "relays" / str(port) / "relay.json"


# ------------------------------------------------------------------- token / state

def read_relay_token() -> str | None:
    """Read the relay token, honouring KRYTH_BROWSER_RELAY_TOKEN override."""
    override = os.environ.get(RELAY_TOKEN_ENV)
    if override:
        return override.strip()
    try:
        return relay_token_path().read_text(encoding="utf-8").strip() or None
    except (OSError, FileNotFoundError):
        return None


def read_relay_state(port: int = DEFAULT_PORT) -> dict[str, Any] | None:
    """Read relay.json state for a port, if present."""
    try:
        return json.loads(relay_state_path(port).read_text(encoding="utf-8"))
    except (OSError, FileNotFoundError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------------- client

class RelayClient:
    """Thin async HTTP client over the browse relay API."""

    def __init__(self, port: int, token: str) -> None:
        self.port = port
        self.token = token
        self.base_url = f"http://127.0.0.1:{port}"
        self._client: httpx.AsyncClient | None = None

    async def _aclient(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "OpenCode-Browser-Client-Pid": str(os.getpid()),
        }
        if extra:
            headers.update(extra)
        return headers

    async def get_version(self) -> dict[str, Any] | None:
        """Probe the relay: returns version info when reachable, else None."""
        try:
            client = await self._aclient()
            resp = await client.get(f"{self.base_url}/version")
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("version"):
                    return data
        except Exception as exc:  # noqa: BLE001 — any failure means "not reachable"
            logger.debug("relay /version probe failed on port %s: %s", self.port, exc)
        return None

    async def get_tools(self) -> list[dict[str, Any]]:
        """List the relay's tool specs."""
        try:
            client = await self._aclient()
            resp = await client.get(
                f"{self.base_url}/tools", headers=self._headers()
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
        except Exception as exc:  # noqa: BLE001
            logger.debug("relay /tools failed: %s", exc)
        return []

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Invoke a relay tool and return its parsed JSON response."""
        body = {"name": name, "arguments": arguments or {}}
        client = await self._aclient()
        resp = await client.post(
            f"{self.base_url}/tools/call",
            headers=self._headers(),
            json=body,
        )
        if resp.status_code == 200:
            return resp.json()
        detail = resp.text.strip()[:500]
        raise RuntimeError(f"relay returned {resp.status_code} for {name}: {detail}")

    async def shutdown(self) -> bool:
        """Ask the relay to shut down.  Returns True when it accepted."""
        try:
            client = await self._aclient()
            resp = await client.post(
                f"{self.base_url}/shutdown",
                headers=self._headers(),
                json={},
            )
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug("relay shutdown failed: %s", exc)
            return False

    async def extension_status(self, waiting: bool = False) -> dict[str, Any] | None:
        """Return the extension's status, if a relay is running."""
        try:
            extra = {"X-Browse-Install": "waiting"} if waiting else {}
            client = await self._aclient()
            resp = await client.get(
                f"{self.base_url}/extension/status",
                headers=self._headers(extra),
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.debug("relay /extension/status failed: %s", exc)
        return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
