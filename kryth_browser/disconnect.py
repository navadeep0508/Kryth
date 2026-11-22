"""BrowserDisconnectTool — disconnect from the browser / stop the relay."""

from __future__ import annotations

import logging
from typing import Any

from config.config import Config
from tools.base import Tool, ToolConfirmation, ToolInvocation, ToolKind, ToolResult

from .manager import BrowserManager

logger = logging.getLogger(__name__)


class BrowserDisconnectTool(Tool):
    """Disconnect from the KRYTH Browser and stop the relay if we started it."""

    name = "browser_disconnect"
    description = "Disconnect from the KRYTH Browser and stop the relay if KRYTH started it."
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
            await self.manager.stop_relay()
            self._connected = False
            return ToolResult.success_result("KRYTH Browser disconnected and relay stopped.")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Browser disconnect failed")
            return ToolResult.error_result(f"Browser disconnect failed: {exc}")
