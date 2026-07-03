"""MCPManager — configure, start, and discover tools from MCP servers.

Stores config in ``~/.kryth/config.json`` under ``"mcps"`` key.
Each entry is:
.. code:: json

    {
      "my-server": {
        "type": "stdio",
        "command": "npx",
        "args": ["-y", "@org/mcp-server"]
      },
      "my-db": {
        "type": "sse",
        "url": "http://localhost:8000/mcp"
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False


# ── Config path ───────────────────────────────────────────────────────────

def _config_path() -> Path:
    return Path.home() / ".kryth" / "config.json"


def _load_raw() -> dict[str, Any]:
    path = _config_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_raw(data: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except Exception:
        pass


# ── MCP config helpers ────────────────────────────────────────────────────

def get_mcp_configs() -> dict[str, dict[str, Any]]:
    """Return all saved MCP server configs."""
    raw = _load_raw()
    return dict(raw.get("mcps", {}))


def _save_mcp_configs(configs: dict[str, dict[str, Any]]) -> None:
    raw = _load_raw()
    raw["mcps"] = configs
    _save_raw(raw)


def add_mcp_config(name: str, config: dict[str, Any]) -> None:
    """Save or update an MCP server configuration."""
    configs = get_mcp_configs()
    configs[name] = config
    _save_mcp_configs(configs)


def remove_mcp_config(name: str) -> bool:
    """Remove an MCP server config. Returns True if it existed."""
    configs = get_mcp_configs()
    if name not in configs:
        return False
    del configs[name]
    _save_mcp_configs(configs)
    return True


# ── MCP server discovery ──────────────────────────────────────────────────

def _to_tool_spec(tool: Any, server_name: str) -> dict[str, Any]:
    """Convert an MCP Tool object to the KRYTH tool spec format."""
    schema = dict(tool.inputSchema) if hasattr(tool, "inputSchema") else {"type": "object"}
    if "properties" not in schema:
        schema["properties"] = {}
    if "type" not in schema:
        schema["type"] = "object"
    return {
        "function": {
            "name": tool.name,
            "description": f"[MCP {server_name}] {tool.description or ''}",
            "parameters": schema,
        },
        "type": "function",
        "mcp_server": server_name,
    }


def _discover_stdio(
    server_name: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Connect to a stdio-based MCP server and list its tools.

    Returns tool specs in KRYTH format, or an empty list on failure.
    """
    if not MCP_AVAILABLE:
        return []

    result: list[dict[str, Any]] = []

    async def _run() -> list[dict[str, Any]]:
        server_params = StdioServerParameters(
            command=command,
            args=args,
            env={**os.environ, **(env or {})},
        )
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    return [
                        _to_tool_spec(t, server_name)
                        for t in tools_result.tools
                    ]
        except Exception as exc:
            return []

    try:
        result = asyncio.run(_run())
    except Exception:
        result = []
    return result


def _discover_sse(
    server_name: str,
    url: str,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """Connect to an SSE-based MCP server and list its tools."""
    if not MCP_AVAILABLE:
        return []

    async def _run() -> list[dict[str, Any]]:
        try:
            async with sse_client(url=url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_result = await session.list_tools()
                    return [
                        _to_tool_spec(t, server_name)
                        for t in tools_result.tools
                    ]
        except Exception as exc:
            return []

    try:
        return asyncio.run(_run())
    except Exception:
        return []


def discover_tools(
    server_name: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Discover tools from a configured MCP server.

    ``config`` must contain ``"type"`` (``"stdio"`` or ``"sse"``) and
    the appropriate connection details.

    Returns tool specs in KRYTH format.
    """
    server_type = config.get("type", "stdio")
    if server_type == "sse":
        url = config.get("url", "")
        if not url:
            return []
        return _discover_sse(server_name, url)
    command = config.get("command", "")
    args = config.get("args", [])
    env = config.get("env")
    if not command:
        return []
    return _discover_stdio(server_name, command, args, env=env)


def discover_all_tools() -> dict[str, list[dict[str, Any]]]:
    """Discover tools from every configured MCP server.

    Returns ``{server_name: [tool_specs]}``.
    """
    results: dict[str, list[dict[str, Any]]] = {}
    for name, config in get_mcp_configs().items():
        tools = discover_tools(name, config)
        if tools:
            results[name] = tools
    return results


# ── MCPManager class ───────────────────────────────────────────────────────

class MCPManager:
    """Manages MCP server configs and tool discovery for the agent.

    Usage::

        mgr = MCPManager()
        mgr.add_stdio("fs", "npx", ["-y", "@mcp/server-filesystem", "."])
        mgr.add_sse("db", "http://localhost:8000/mcp")
        mgr.list_servers()
        mgr.remove_server("fs")
    """

    def add_stdio(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Add a stdio-based MCP server configuration."""
        config: dict[str, Any] = {"type": "stdio", "command": command}
        if args:
            config["args"] = args
        if env:
            config["env"] = env
        add_mcp_config(name, config)

    def add_sse(self, name: str, url: str) -> None:
        """Add an SSE-based MCP server configuration."""
        add_mcp_config(name, {"type": "sse", "url": url})

    def remove_server(self, name: str) -> bool:
        """Remove a server configuration."""
        return remove_mcp_config(name)

    def list_servers(self) -> dict[str, dict[str, Any]]:
        """Return all configured MCP servers."""
        return get_mcp_configs()

    def server_tools(self, name: str) -> list[dict[str, Any]]:
        """Discover tools from a specific MCP server."""
        configs = get_mcp_configs()
        config = configs.get(name)
        if not config:
            return []
        return discover_tools(name, config)

    def all_tools(self) -> list[dict[str, Any]]:
        """Discover tools from every configured MCP server."""
        specs: list[dict[str, Any]] = []
        for name, config in get_mcp_configs().items():
            specs.extend(discover_tools(name, config))
        return specs

    def build_tool_specs(self) -> list[dict[str, Any]]:
        """Return MCP tool specs formatted for agent TOOL_SPECS injection.

        Each tool has an ``mcp_server`` field so the dispatcher can route
        calls to the right MCP server.
        """
        return self.all_tools()


_MCP_MANAGER: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    global _MCP_MANAGER
    if _MCP_MANAGER is None:
        _MCP_MANAGER = MCPManager()
    return _MCP_MANAGER


# ── MCP tool dispatch ─────────────────────────────────────────────────────

def call_mcp_tool(
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Call a tool on an MCP server and return its text result.

    This is a synchronous facade over the async MCP SDK.
    Returns the tool output text, or an error message.
    """
    if not MCP_AVAILABLE:
        return "Error: MCP SDK not available (pip install mcp)"

    configs = get_mcp_configs()
    config = configs.get(server_name)
    if not config:
        return f"Error: MCP server '{server_name}' not configured"

    server_type = config.get("type", "stdio")

    async def _call_stdio() -> str:
        server_params = StdioServerParameters(
            command=config["command"],
            args=config.get("args", []),
            env={**os.environ, **(config.get("env") or {})},
        )
        try:
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments or {})
                    if result.isError:
                        return f"Error: {result.content[0].text if result.content else 'unknown'}"
                    texts = [
                        c.text for c in (result.content or [])
                        if hasattr(c, "text")
                    ]
                    return "\n".join(texts) if texts else "(no output)"
        except Exception as exc:
            return f"Error calling MCP tool {server_name}/{tool_name}: {exc}"

    async def _call_sse() -> str:
        try:
            async with sse_client(url=config["url"]) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments or {})
                    if result.isError:
                        return f"Error: {result.content[0].text if result.content else 'unknown'}"
                    texts = [
                        c.text for c in (result.content or [])
                        if hasattr(c, "text")
                    ]
                    return "\n".join(texts) if texts else "(no output)"
        except Exception as exc:
            return f"Error calling MCP tool {server_name}/{tool_name}: {exc}"

    try:
        if server_type == "sse":
            return asyncio.run(_call_sse())
        return asyncio.run(_call_stdio())
    except Exception as exc:
        return f"Error calling MCP tool {server_name}/{tool_name}: {exc}"
