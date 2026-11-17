"""Live browser test using the KUBA engine against a real Chrome instance.

This test requires Chrome to be running with remote debugging enabled
(e.g. `chrome.exe --remote-debugging-port=9222`). It is skipped when no
CDP endpoint is reachable.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

from tools.kuba_browser_engine import KUBABrowserEngine

KUBA_SRC = Path(__file__).resolve().parents[1] / "browser" / "src"


def _kuba_available() -> bool:
    """Return True when the KUBA browser source can be imported."""
    return (KUBA_SRC / "kuba_browser" / "helpers.py").is_file()


def _cdp_reachable() -> bool:
    """Return True when a Chrome CDP endpoint answers on localhost:9222."""
    import urllib.request

    try:
        urllib.request.urlopen("http://localhost:9222/json/version", timeout=3)
        return True
    except Exception:
        return False


@pytest.fixture
def kuba_engine():
    if not _kuba_available():
        pytest.skip("KUBA browser source not present")
    if not _cdp_reachable():
        pytest.skip("Chrome CDP endpoint not reachable on localhost:9222")

    sys.path.insert(0, str(KUBA_SRC))
    helpers = importlib.import_module("kuba_browser.helpers")
    admin = importlib.import_module("kuba_browser.admin")
    workspace = Path(__file__).resolve().parents[1] / "browser" / "agent-workspace"
    os.environ["BU_CDP_URL"] = "http://localhost:9222"

    engine = KUBABrowserEngine(
        helpers=helpers,
        ensure_daemon=admin.ensure_daemon,
        workspace=workspace,
        timeout_seconds=30.0,
    )
    try:
        yield engine
    finally:
        try:
            import asyncio
            asyncio.run(engine.disconnect())
        except Exception:
            pass


@pytest.mark.asyncio
async def test_kuba_live_connect_and_navigate_youtube(kuba_engine):
    """Connect to Chrome via CDP, navigate to YouTube, and verify the page loads."""
    result = await kuba_engine.connect()
    assert result["connected"] is True
    assert result["browser_kind"] == "kuba-cdp"
    assert result["tab_count"] >= 1

    nav = await kuba_engine.navigate("https://www.youtube.com", wait_for_load=True)
    assert nav["url"].startswith("https://www.youtube.com")
    assert nav["document_ready"] is True
    assert nav["title"]

    page = await kuba_engine.observe(mode="compact")
    assert page["url"].startswith("https://www.youtube.com")
    # YouTube is a SPA: body.innerText may be empty until hydration, but URL,
    # title, and readyState already confirm the page loaded over live CDP.
    assert page["title"]
    assert page["readyState"] == "complete"
