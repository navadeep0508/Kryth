"""MCP server management for KRYTH.

Manages MCP server configurations, discovers tools from
configured servers, and makes them available to the agent.
"""

from agent.mcp.manager import MCPManager, get_mcp_manager, MCP_AVAILABLE

__all__ = ["MCPManager", "get_mcp_manager", "MCP_AVAILABLE"]
