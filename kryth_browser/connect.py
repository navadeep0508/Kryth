"""BrowserConnectTool — connect to / report browser status via the relay."""

from __future__ import annotations

import logging
from typing import Any

from config.config import Config
from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult

from .manager import BrowserManager

logger = logging.getLogger(__name__)


class BrowserConnectTool(Tool):
    """Connect to the KRYTH Browser relay (or report that it is already connected)."""

    name = "browser_connect"
    description = (
        "Connect to the KRYTH Browser relay, starting it if necessary.  Returns "
        "the relay version and whether a browser/extension is paired."
    )
    kind = ToolKind.NETWORK

    def __init__(self, config: Config, manager: BrowserManager) -> None:
        super().__init__(config)
        self.manager = manager
        self._connected = False

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return []

    def is_mutating(self, params: dict[str, Any]) -> bool:
        return False

    async def get_confirmation(self, invocation: ToolInvocation) -> ToolConfirmation | None:
        return None

    async def execute(self, invocation: ToolInvocation) -> ToolResult:
        try:
            client = await self._ensure_client()
            version = await client.get_version()
            status = await client.extension_status(waiting=False) or {}
            self._connected = True
            return ToolResult.success_result(
                f"KRYTH Browser connected (relay v{version.get('version', '?')} "
                f"on port {client.port}). "
                f"Extension: {status.get('connectionState', 'unknown')}."
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Browser connect failed")
            return ToolResult.error_result(f"Browser connect failed: {exc}")

    async def _ensure_client(self):
        client = self.manager.get_client()
        if client is not None:
            self._connected = True
            return client
        started = await self.manager.start_relay()
        if not started:
            raise RuntimeError(
                "KRYTH Browser relay could not be started. Run `kryth browser doctor`."
            )
        client = self.manager.get_client()
        if client is None:
            raise RuntimeError("Relay started but no client available.")
        self._connected = True
        return client
