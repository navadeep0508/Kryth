with open('kryth/src/agent/agent_loop.py', 'rb') as f:
    lines = f.read().split(b'\n')

# Fix lines 1276-1277: add 4 spaces
lines[1275] = b'    if _total_tool_calls == 0:\r'
lines[1276] = b'        return LoopResult(status="done", content=content, turns_used=turn_count, finish_reason="completed")\r'

# Fix lines 1259-1265: scratchpad block — add 4 spaces
for i in range(1258, 1266):
    line = lines[i]
    if line.strip():
        stripped = line.lstrip()
        lines[i] = b'    ' + stripped

with open('kryth/src/agent/agent_loop.py', 'wb') as f:
    f.write(b'\n'.join(lines))

import py_compile
py_compile.compile('kryth/src/agent/agent_loop.py', doraise=True)
print('Indentation fixed and compiled OK')
