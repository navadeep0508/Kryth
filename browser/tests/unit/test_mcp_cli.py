"""Tests for the optional MCP console entry point."""

import sys

import pytest

import kuba_browser
from kuba_browser import mcp_cli


def test_main_explains_missing_mcp_extra(monkeypatch):
    monkeypatch.delitem(sys.modules, "kuba_browser.mcp_server", raising=False)
    monkeypatch.delattr(kuba_browser, "mcp_server", raising=False)
    monkeypatch.delitem(sys.modules, "mcp.server", raising=False)
    monkeypatch.setitem(sys.modules, "mcp", None)

    with pytest.raises(SystemExit, match=r"pip install 'kuba-browser\[mcp\]'"):
        mcp_cli.main()
