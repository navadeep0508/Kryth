import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')
from agent.agent_loop import dispatch_tool_call, TOOLS
from agent.session import Session

print("Available tools:", list(TOOLS.keys())[:5], "...")

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
try:
    dispatch_tool_call(s, call)
    print("Messages:", len(s.messages))
    for m in s.messages:
        print("Message:", m.get("role"), m.get("name"), m.get("tool_call_id"))
except Exception as e:
    print("Exception:", e)
    import traceback
    traceback.print_exc()