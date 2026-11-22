"""BrowserStatusTool — return the current browser/relay connection status."""

from __future__ import annotations

import logging
from typing import Any

from config.config import Config
from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult

from .manager import BrowserManager

logger = logging.getLogger(__name__)


class BrowserStatusTool(Tool):
    """Report whether the KRYTH Browser relay is running and what tabs exist."""

    name = "browser_status"
    description = "Return the current KRYTH Browser relay and connection status."
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
            client = self.manager.get_client()
            if client is None:
                return ToolResult.success_result(
                    "KRYTH Browser: not connected. Relay is not running. "
                    "Run `kryth browser setup` or `browser_connect` to connect."
                )
            version = await client.get_version()
            status = await client.extension_status(waiting=False) or {}
            self._connected = True
            return ToolResult.success_result(
                f"KRYTH Browser status: connected=True, "
                f"relay=v{version.get('version', '?')} port={client.port}, "
                f"extension={status.get('connectionState', 'unknown')}"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Browser status failed")
            return ToolResult.error_result(f"Browser status failed: {exc}")
