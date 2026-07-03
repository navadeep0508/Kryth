with open('kryth/src/agent/agent_loop.py', 'rb') as f:
    data = f.read()
lines = data.split(b'\n')
lines[1275] = b'    if _total_tool_calls == 0:\r'
lines[1276] = b'        return LoopResult(status="done", content=content, turns_used=turn_count, finish_reason="completed")\r'  # noqa: E501
with open('kryth/src/agent/agent_loop.py', 'wb') as f:
    f.write(b'\n'.join(lines))
print('Fixed')
