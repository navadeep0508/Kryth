with open('agent_loop.py') as f:
    lines = f.readlines()
for i in range(524, 595):
    lines[i] = '    ' + lines[i]
with open('agent_loop.py', 'w') as f:
    f.writelines(lines)
print('fixed')
