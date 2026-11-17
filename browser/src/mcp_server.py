"""Compatibility module for running the KUBA MCP server directly."""

from kuba_browser.mcp_server import SERVER, main

__all__ = ["SERVER", "main"]

if __name__ == "__main__":
    main()
