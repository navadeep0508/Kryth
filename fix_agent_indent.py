with open('kryth/src/agent/agent_loop.py', 'rb') as f:
    lines = f.read().split(b'\n')

# Fix 1: dedented too much
lines[1256] = b'                _consecutive_no_tool_turns += 1'

# Fix 2: restore normal text-only completion path indentation
lines[1266] = b'    # Text-only (no tool calls) completion path.'
lines[1267] = b'    # No tool calls = the model is either answering a question'
lines[1268] = b'    # conversationally, or it finished its tool work and is'
lines[1269] = b'    # summarizing. Accept as done.'
lines[1270] = b'    # Note: content may be empty in the response dict even when'
lines[1271] = b'    # the LLM streamed text live (common with step/nemotron models'
lines[1272] = b'    # where content goes through _filter_leaks). The streaming UI'
lines[1273] = b'    # already displayed it to the user, so an empty content string'
lines[1274] = b"    # here is fine \xe2\x80\x94 we know the model didn't want tools."
lines[1275] = b'    if _total_tool_calls == 0:'
lines[1276] = b'        return LoopResult(status="done", content=content, turns_used=turn_count, finish_reason="completed")'

# Fix 3: self-eval block + self_eval import should stay intact
lines[1307] = b'    if _total_tool_calls > 0:'
lines[1308] = b'        try:'
lines[1309] = b'            from agent.self_eval import evaluate_task as _self_eval'
lines[1310] = b'            _task_desc = next('
lines[1311] = b'                (m.get("content", "") for m in session.messages'
lines[1312] = b'                if m.get("role") == "user" and isinstance(m.get("content"), str)),'
lines[1313] = b'                "",'
lines[1314] = b'            )'

# Delete duplicate block lines if present
for idx in range(1309, 1320):
    line = lines[idx].lstrip()
    if line.startswith(b'from agent.runtime.scratchpad'):
        lines[idx] = b''
    elif line.startswith(b'if getattr(_scratch.state'):
        lines[idx] = b''
    elif line.startswith(b'_block = _scratch.render_prompt_block()'):
        lines[idx] = b''
    elif line.startswith(b'session.messages = [m for m'):
        lines[idx] = b''
    elif line.startswith(b'session.messages.insert(-1'):
        lines[idx] = b''

with open('kryth/src/agent/agent_loop.py', 'wb') as f:
    f.write(b'\n'.join(lines))

import py_compile
py_compile.compile('kryth/src/agent/agent_loop.py', doraise=True)
print('Compiled OK')
