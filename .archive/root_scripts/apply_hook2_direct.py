with open('kryth/src/agent/agent_loop.py', 'rb') as f:
    lines = f.read().split(b'\n')

# Line 1700 (index 1699) = " if _is_first_turn:"
# Lines 1701-1703 are the block to keep
# Line 1705 (index 1704) = " # Wait briefly..."
# We will insert new lines between 1703 and 1705

insert = [
    b'',
    b'        try:',
    b'            from agent.runtime.scratchpad import scratch as _scratch',
    b'            _scratch.initialize(user_input)',
    b'        except Exception:',
    b'            pass',
]

new_lines = lines[:1704] + insert + lines[1704:]

with open('kryth/src/agent/agent_loop.py', 'wb') as f:
    f.write(b'\n'.join(new_lines))

import py_compile
py_compile.compile('kryth/src/agent/agent_loop.py', doraise=True)
print('Hook 2 applied')
