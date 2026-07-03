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
dispatch_tool_call(s, call)
for m in reversed(s.messages):
    if m.get("role") == "tool" and m.get("tool_call_id") == "test1":
        print("TOOL CONTENT:")
        print(m.get("content"))
        break
