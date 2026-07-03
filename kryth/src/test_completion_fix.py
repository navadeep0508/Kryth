import sys
import os
os.chdir(r'C:\Users\navadeep\Documents\Kryth\kryth\src')

from agent.benchmarks.suite import run_single, get_prompts

print("Running a MODIFY task to test post-tool completion detection...")
p = get_prompts('MODIFY')[5]  # add_error_handling task
p.setup()

from agent.session import get_session
session = get_session()
session.reset()

r = run_single(p)

print(f'\nSTATUS: {r.status}')
print(f'TURNS: {r.turns_used}')
print(f'TOOLS: {r.tool_calls}')
print(f'ERROR: {r.error}')
print(f'TOKENS: {r.tokens_in} in / {r.tokens_out} out')
print(f'BLOCKERS: {session.blockers if hasattr(session, "blockers") else "none"}')

# Cleanup
p.teardown()

sys.exit(0 if r.status == 'done' else 1)