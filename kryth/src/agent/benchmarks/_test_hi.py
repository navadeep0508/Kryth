from agent.agent_loop import run_agent, LoopResult
r = run_agent("hi")
print(f"Status: {getattr(r, 'status', '?')}, turns: {getattr(r, 'turns_used', '?')}")
