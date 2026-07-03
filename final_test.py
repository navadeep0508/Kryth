import sys
sys.path.insert(0, 'kryth/src')
from agent.task_classifier import classify_task
from agent.handlers.search_handler import run_search
from agent.handlers.read_handler import summarize_project
from agent.handlers.run_handler import detect_stack, run_and_verify

print('=== CLASSIFIER ===')
tests = [
    'find routes',
    'trace auth flow', 
    'search config',
    'locate login logic',
    'where is the api',
    'explain app.py',
    'create hello.py',
    'run this project',
    'read this project',
    'what is python',
]
for t in tests:
    prof = classify_task(t)
    print(f'  {t:25s} -> intent={prof.intent:8s} complexity={prof.complexity:8s} conv={prof.is_conversational}')

print()
print('=== SEARCH HANDLER ===')
for q in ['find routes', 'trace auth flow']:
    r = run_search(q, 'kryth/src/agent')
    print(f'  {q:20s} -> matches={r["matches"]:3d} files_read={r["files_read"]} conf={r["confidence"]:.2f}')

print()
print('=== READ HANDLER ===')
r = summarize_project('kryth/src/agent')
print(f'  {r[:80]}...')

print()
print('=== RUN HANDLER ===')
stack = detect_stack('.')
print(f'  Language: {stack["language"]}, Run: {stack["run_cmd"]}')