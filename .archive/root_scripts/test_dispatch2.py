import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')
from agent.session import Session
from agent.permissions import check_permission
from agent.agent_loop import execute_tool, _coerce_tool_args

session = Session()
tool_name = 'write_file'
args = {"path": "test_dispatch.txt", "content": "hello"}

print("Checking permission...")
permitted, reason = check_permission(tool_name, args)
print(f"Permitted: {permitted}, reason: {reason}")

print("Coercing args...")
args = _coerce_tool_args(tool_name, args)
print(f"Coerced args: {args}")

print("Executing tool...")
from agent.agent_loop import execute_tool
result = execute_tool(tool_name, args)
print(f"Result: {result}")
print('Done')