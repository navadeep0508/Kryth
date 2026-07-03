with open('src/agent/agent_loop.py') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if 'tool_name == "read_file"' in line and 'is_error' in line:
        print(f'L{i+1}: {repr(lines[i].strip())}')
        break
