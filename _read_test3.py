import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')
from agent.agent_loop import dispatch_tool_call
from agent.session import Session

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
dispatch_tool_call(s, call)
print("Messages:", len(s.messages))
for m in reversed(s.messages):
    print("Message:", m.get("role"), m.get("name"), m.get("tool_call_id"))
    if m.get("role") == "tool" and m.get("tool_call_id") == "test1":
        print("TOOL CONTENT:")
        print(m.get("content"))
        break