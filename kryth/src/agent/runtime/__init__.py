"""Agent Runtime package."""

from agent.runtime.adapter import run_agent_adapter as run_agent
from agent.runtime.state import AgentState


def run_agent_simple(user_input: str) -> str:
    """Simple run returning just the content string."""
    result = run_agent(user_input)
    return result.content or ""


__all__ = ["run_agent", "run_agent_simple", "AgentState"]