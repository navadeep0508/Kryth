import sys
sys.path.insert(0, 'kryth/src')

from agent.task_classifier import classify_task, has_execution_intent

benchmarks = [
    ("read this project", "1. read the project"),
    ("explain app.py", "2. explain a file"),
    ("find routes", "3. find/search"),
    ("create hello.py", "4. create a file"),
    ("run this project", "5. run project"),
    ("trace auth flow", "6. trace flow"),
    ("explain architecture", "7. explain architecture"),
    ("what is python", "8. knowledge question"),
]

print(f"{'Input':30s} {'Conv':6s} {'Intent':6s} {'Complex':9s} {'Category':12s}")
print("-"*70)
for text, label in benchmarks:
    prof = classify_task(text)
    intent = has_execution_intent(text)
    print(f"{text:30s} {str(prof.is_conversational):6s} {str(intent):6s} {prof.complexity:9s} {prof.category:12s}")

print()
print("=== Tool curation intent group mapping ===")
from agent.tool_curator import _pick_intent_group, select_domains, _group_name
for text, label in benchmarks:
    domains = select_domains(text)
    group = _pick_intent_group(text, domains)
    print(f"{text:30s} -> {_group_name(group)} ({len(group)} tools)")
