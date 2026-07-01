"""Runtime state - minimal re-export for V1 compatibility."""

from agent.session import Session

# Alias for backward compatibility
AgentState = Session

__all__ = ["AgentState"]