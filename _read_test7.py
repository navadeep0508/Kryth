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

import agent.agent_loop as al

# Trace all returns from dispatch_tool_call
original_dispatch = al.dispatch_tool_call
def traced_dispatch(*args, **kwargs):
    print("TRACE: dispatch_tool_call entered")
    try:
        result = original_dispatch(*args, **kwargs)
        print("TRACE: dispatch_tool_call returned normally")
        return result
    except Exception as e:
        print("TRACE: dispatch_tool_call raised:", e)
        raise

al.dispatch_tool_call = traced_dispatch

# Trace execute_tool
original_execute = al.execute_tool
def traced_execute(*args, **kwargs):
    print("TRACE: execute_tool entered")
    try:
        result = original_execute(*args, **kwargs)
        print("TRACE: execute_tool returned:", type(result), str(result)[:100])
        return result
    except Exception as e:
        print("TRACE: execute_tool raised:", e)
        raise

al.execute_tool = traced_execute

# Trace _append_tool_msg
original_append = al._append_tool_msg
def traced_append(*args, **kwargs):
    print("TRACE: _append_tool_msg called")
    return original_append(*args, **kwargs)
al._append_tool_msg = traced_append

s = Session()
s.read_memory = {}

call = {
    "id": "test1",
    "function": {
        "name": "read_file",
        "arguments": '{"path": "C:/Users/navadeep/Documents/Kryth/test_hello.py", "limit": 60}'
    }
}

try:
    print("Calling dispatch_tool_call...")
    al.dispatch_tool_call(s, call)
    print("Messages:", len(s.messages))
    for m in s.messages:
        print("Message:", m.get("role"), m.get("name"), m.get("tool_call_id"))
except Exception as e:
    print("Exception:", e)
    import traceback
    traceback.print_exc()