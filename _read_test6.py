import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')
from agent.agent_loop import dispatch_tool_call, execute_tool
from agent.session import Session
from agent.tools import TOOLS
from agent import ui

# Wrap s.append to trace
s = Session()
s.read_memory = {}

call = {
    "id": "test1",
    "function": {
        "name": "read_file",
        "arguments": '{"path": "C:/Users/navadeep/Documents/Kryth/test_hello.py", "limit": 60}'
    }
}

print("Calling dispatch_tool_call...")

# Monkey-patch _append_tool_msg to trace
import agent.agent_loop as al
original_append = al._append_tool_msg
def traced_append(*args, **kwargs):
    print("TRACE: _append_tool_msg called with:", args[3][:100] if len(args) > 3 else args)
    return original_append(*args, **kwargs)
al._append_tool_msg = traced_append

# Also trace execute_tool
original_execute = al.execute_tool
def traced_execute(*args, **kwargs):
    print("TRACE: execute_tool called")
    return original_execute(*args, **kwargs)
al.execute_tool = traced_execute

try:
    dispatch_tool_call(s, call)
    print("Messages:", len(s.messages))
    for m in s.messages:
        print("Message:", m.get("role"), m.get("name"), m.get("tool_call_id"))
except Exception as e:
    print("Exception:", e)
    import traceback
    traceback.print_exc()