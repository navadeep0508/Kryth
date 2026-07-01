"""Spawn focused subagents — single or in parallel — in isolated sessions."""

from __future__ import annotations

from agent import ui
from agent.tools._results import err


SUBAGENT_SYSTEM_SUFFIX = (
    "You are a subagent spawned by the main agent. "
    "Focus only on the task below. When finished, reply with a "
    "concise final summary and stop calling tools."
)


def _build_nested(description: str, prompt: str, parent_depth: int, parent_can_spawn: bool = True, parent_profile: str = "default"):
    """Construct a fresh Session for a subagent at depth = parent+1."""
    from agent.session import Session
    from agent.prompts import SYSTEM_PROMPT

    nested = Session()
    nested.system_prompt = f"{SYSTEM_PROMPT}\n\n{SUBAGENT_SYSTEM_SUFFIX}"
    nested.ensure_system()
    nested.append({
        "role": "user",
        "content": f"[Task from parent agent: {description}]\n\n{prompt}",
    })
    nested.depth = parent_depth + 1
    # All subagents (depth >= 1) are workers and cannot spawn further agents.
    # Only the root coordinator (depth=0) has can_spawn=True.
    nested.can_spawn = False
    # Inherit parent's permission profile so yolo/auto runs stay unattended.
    nested.profile = parent_profile
    return nested


def _summarize_result(result, max_turns: int) -> str:
    if result.status == "interrupted":
        return "(subagent interrupted)"
    if result.status == "api_error":
        return "(subagent failed due to an LLM API error)"
    if result.status == "max_turns":
        return (
            f"(subagent reached the {max_turns}-turn limit without finishing; "
            f"used {result.turns_used} turns)"
        )
    return result.content or ""






