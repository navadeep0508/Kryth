import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')
from agent.agent_loop import dispatch_tool_call, execute_tool
from agent.session import Session
from agent.tools import TOOLS

# Test execute_tool directly
result = execute_tool("read_file", {"path": "C:/Users/navadeep/Documents/Kryth/test_hello.py", "limit": 60})
print("Direct execute_tool result:", result[:200] if result else "None")

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
s = Session()
s.read_memory = {}

# Add some debug to trace
from agent import ui
original_tool_start = ui.tool_start
def debug_tool_start(*args, **kwargs):
    print("DEBUG: ui.tool_start", args, kwargs)
    return original_tool_start(*args, **kwargs)
ui.tool_start = debug_tool_start

original_append = s.append
def debug_append(*args, **kwargs):
    print("DEBUG: s.append", args, kwargs)
    return original_append(*args, **kwargs)
s.append = debug_append

dispatch_tool_call(s, call)
print("Messages:", len(s.messages))
for m in s.messages:
    print("Message:", m.get("role"), m.get("name"), m.get("tool_call_id"))
    if m.get("role") == "tool":
        print("  content:", m.get("content")[:200] if m.get("content") else "None")