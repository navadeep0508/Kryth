import sys
sys.path.insert(0, 'kryth/src')
from agent.session import Session, get_session, push_session, pop_session
from agent._subagent import spawn_agent
from unittest.mock import MagicMock
import agent.agent_loop
agent.agent_loop.run_inner_loop = MagicMock(return_value=MagicMock(status='done', content='OK'))

print("Initial current session:", get_session(), "depth:", get_session().depth)

parent = Session()
parent.depth = 0
parent.can_spawn = True
token = push_session(parent)
print("After push parent, current session id:", id(get_session()), "depth:", get_session().depth)
print("Parent session id:", id(parent), "parent.depth:", parent.depth)

result = spawn_agent("test", "test prompt", max_turns=1)
print("After spawn_agent, current session id:", id(get_session()), "depth:", get_session().depth)
print("Parent session id:", id(parent), "parent.depth:", parent.depth)

current = get_session()
print("Current session is parent?", current is parent)
print("Current depth:", current.depth, "Parent depth:", parent.depth)

assert current.depth == parent.depth, f"Expected depth {parent.depth}, got {current.depth}"
print("Test passed!")

pop_session(token)