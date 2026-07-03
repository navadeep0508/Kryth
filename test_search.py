import sys
sys.path.insert(0, 'kryth/src')
from agent.handlers.search_handler import run_search, _is_search_intent

tests = [
    'find routes',
    'trace auth flow',
    'search config',
    'locate login logic',
    'where is the api',
    'explain app.py',
    'create hello.py',
]

print('=== Search Intent Detection ===')
for t in tests:
    print(f'  {t:30s} -> {_is_search_intent(t)}')

print()
print('=== Search Execution (find routes) ===')
result = run_search('find routes', '.')
print(f'  Status: {result["status"]}')
print(f'  Matches: {result.get("matches", 0)}')
print(f'  Files read: {result.get("files_read", 0)}')
print(f'  Confidence: {result.get("confidence", 0):.2f}')
print(f'  Terminated: {result.get("terminated", False)}')
print('  Summary:')
for line in result.get('summary', '').split('\n')[:10]:
    print(f'    {line}')