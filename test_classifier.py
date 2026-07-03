import sys
sys.path.insert(0, 'kryth/src')
from agent.task_classifier import classify_task

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
    'hi',
]

print('=== Classifier Test ===')
for t in tests:
    prof = classify_task(t)
    print(f'  {t:25s} -> intent={prof.intent:8s} category={prof.category:15s} complexity={prof.complexity:8s} conv={prof.is_conversational}')