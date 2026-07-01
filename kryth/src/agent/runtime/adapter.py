"""Runtime Adapter — simple bridge to V1 agent_loop."""

from __future__ import annotations

from agent.agent_loop import run_agent, LoopResult



def run_agent_adapter(user_input: str, extra_system: str | None = None) -> LoopResult:
    """
    Adapter matching old agent_loop.run_agent() signature.
    Delegates directly to V1 runtime.
    """
    # Conversational gate - removed task_classifier, all inputs go to agent loop
    # All inputs now go directly to the agent loop for processing

    # V1 path
    from agent.agent_loop import run_agent as run_v1
    return run_v1(user_input, extra_system)


def _run_conversational_reply(user_input: str) -> LoopResult:
    """Direct LLM reply for conversational input (no tools)."""
    from agent.llm import ask_llm_stream
    from agent.prompts import SYSTEM_PROMPT

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\nYou are a helpful assistant. Do NOT use any tool calls. Just respond naturally."},
        {"role": "user", "content": user_input},
    ]

    response = ask_llm_stream(messages, tools=None)
    content = response.get("content", "") or "Hello!"

    return LoopResult(
        status="done",
        content=content,
        turns_used=0,
        finish_reason="completed",
    )