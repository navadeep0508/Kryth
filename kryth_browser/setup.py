"""First-run setup wizard for the KRYTH Browser.

Guides the user through:
1. Checking that Chrome/Edge is installed
2. Ensuring the relay binary is built (cargo build --release)
3. Starting the relay and pairing the Chromium extension
4. Verifying end-to-end that a page can be opened and read
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from config.config import Config
from tools.base import ExecutionStatus, ToolResult

from .manager import BrowserManager
from .relay_client import DEFAULT_PORT, read_relay_token


class BrowserSetupWizard:
    """Interactive-first (but scriptable) setup for KRYTH Browser."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.manager = BrowserManager(config)
        self.steps: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ steps

    def _record(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append({"name": name, "ok": ok, "detail": detail})

    async def check_browser_installed(self) -> bool:
        """Check that Chrome or Edge is installed."""
        candidates = []
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ]
        else:
            candidates = [
                shutil.which("google-chrome") or "",
                shutil.which("chromium") or "",
                shutil.which("google-chrome-stable") or "",
                shutil.which("microsoft-edge") or "",
                shutil.which("msedge") or "",
            ]
        found = [c for c in candidates if c and Path(c).exists()]
        ok = bool(found)
        self._record("browser_installed", ok, f"found: {found[0] if found else 'none'}")
        return ok

    async def check_relay_binary(self) -> bool:
        """Check that the pre-built relay binary exists."""
        binary = self.manager.relay_binary_path()
        ok = binary is not None
        self._record("relay_binary", ok, str(binary) if binary else "not built")
        return ok

    async def check_relay_running(self) -> bool:
        """Check whether a relay is already answering on its port."""
        from .manager import BrowserManager as BM
        ok = BM.probe(DEFAULT_PORT)
        self._record("relay_running", ok, f"port {DEFAULT_PORT}")
        return ok

    async def start_relay(self) -> bool:
        """Start the relay and confirm it answers."""
        ok = await self.manager.start_relay()
        self._record("relay_started", ok, "started" if ok else "failed")
        return ok

    async def verify_page(self) -> bool:
        """Open a blank page and read it back, to confirm end-to-end function."""
        client = self.manager.get_client()
        if client is None:
            self._record("page_verify", False, "no relay client")
            return False
        try:
            result = await client.call_tool("navigate", {"url": "about:blank"})
            ok = bool(result.get("url")) or bool(result.get("tab"))
            self._record("page_verify", ok, "about:blank loaded" if ok else "no result")
            return ok
        except Exception as exc:  # noqa: BLE001
            self._record("page_verify", False, str(exc))
            return False

    # ------------------------------------------------------------------ run

    async def run(self) -> ToolResult:
        """Run all setup steps and return a ToolResult summary."""
        results = []
        results.append(await self.check_browser_installed())
        results.append(await self.check_relay_binary())
        results.append(await self.check_relay_running())
        if not results[1]:
            # Binary missing: tell the user how to build it.
            return ToolResult.error_result(
                "KRYTH Browser relay binary not built. "
                "Run: cargo build --release  (in kryth_browser/vendor/rust/)",
                execution_status=ExecutionStatus.ACTION_FAILED,
                metadata={"steps": self.steps},
            )
        results.append(await self.start_relay())
        results.append(await self.verify_page())
        # Core setup is complete once browser + binary + relay are confirmed.
        # verify_page requires an extension-paired browser, which is a manual
        # user step — record it but don't block setup on it.
        core_ok = all(results[:4])
        page_ok = results[4]
        if core_ok:
            detail = "Relay running and ready."
            if page_ok:
                detail += " Test page loaded successfully."
            else:
                detail += " Pair the Chromium extension from kryth_browser/vendor/ for browser automation."
            return ToolResult.success_result(
                f"KRYTH Browser setup complete. {detail}",
                execution_status=ExecutionStatus.TASK_COMPLETED,
                metadata={"steps": self.steps, "relay_port": DEFAULT_PORT},
            )
        return ToolResult.error_result(
            "KRYTH Browser setup incomplete. See step details above.",
            execution_status=ExecutionStatus.ACTION_FAILED,
            metadata={"steps": self.steps},
        )
