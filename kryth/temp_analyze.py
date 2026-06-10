with open('src/agent/agent_loop.py', 'r') as f:
    lines = f.readlines()

# Find auto_select_skills fallback block
for i in range(len(lines)):
    if i >= 820 and lines[i].strip() == 'else:' and i+1 < len(lines) and 'from agent.skills import auto_select_skills' in lines[i+1]:
        start = i
        end = i
        for j in range(i, min(i+20, len(lines))):
            if 'compose_skills(auto)' in lines[j]:
                end = j
                break
        print('AUTO_SELECT_BLOCK: lines', start+1, 'to', end+1)
        print(''.join(lines[start:end+1]))
        break

# Find parallel_builder block
for i in range(len(lines)):
    if 'if _complexity == "complex":' in lines[i]:
        start = i
        end = i
        for j in range(i, min(i+100, len(lines))):
            if 'elif _complexity == "medium":' in lines[j]:
                end = j - 1
                break
        print('PARALLEL_BUILDER_BLOCK: lines', start+1, 'to', end+1)
        print(''.join(lines[start:end+1]))
        break