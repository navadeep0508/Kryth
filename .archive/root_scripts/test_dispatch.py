import sys
sys.path.insert(0, r'C:\Users\navadeep\Documents\Kryth\kryth\src')
from agent.agent_loop import dispatch_tool_call
from agent.session import Session

session = Session()
call = {
    'id': 'test123',
    'function': {
        'name': 'write_file',
        'arguments': '{"path": "test_dispatch.txt", "content": "hello"}'
    }
}

result = dispatch_tool_call(session, call)
print('Done')