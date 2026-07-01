"""Agent package — unified runtime and tool registry.

Runtime API:
- run_agent: main entry point
- AgentState: canonical runtime state

Tool Registry:
- TOOLS, TOOL_SPECS, READ_ONLY_TOOLS
"""

from __future__ import annotations

# Runtime API
from agent.runtime import run_agent, run_agent_simple, AgentState

# Tool Registry (import from consolidated tools package)
from agent.tools import (
    TOOLS,
    TOOL_SPECS,
    READ_ONLY_TOOLS,
    SELF_RENDERED_TOOLS,
    RUN_COMMAND_ERROR_MARKER,
)


__all__ = [
    "run_agent",
    "run_agent_simple",
    "AgentState",
    "TOOLS",
    "TOOL_SPECS",
    "READ_ONLY_TOOLS",
    "SELF_RENDERED_TOOLS",
    "RUN_COMMAND_ERROR_MARKER",
]