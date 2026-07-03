import sys
sys.path.insert(0, 'kryth/src')

from agent.task_classifier import classify_task, has_execution_intent
from agent.tool_curator import curate, _pick_intent_group, select_domains, _group_name

benchmarks = [
    ("read this project", False, True, "CUSTOM(3)"),
    ("explain app.py", False, True, "BUILD"),
    ("find routes", False, True, "SEARCH"),
    ("create hello.py", False, True, "MINIMAL"),
    ("run this project", False, True, "BUILD"),
    ("trace auth flow", False, True, "BUILD"),
    ("what is python", True, False, "BUILD"),
    ("explain architecture", True, False, "BUILD"),
]

print("=== BENCHMARK VALIDATION ===")
all_pass = True
for text, exp_conv, exp_intent, exp_group in benchmarks:
    prof = classify_task(text)
    intent = has_execution_intent(text)
    domains = select_domains(text)
    group = _group_name(_pick_intent_group(text, domains))
    conv_ok = prof.is_conversational == exp_conv
    intent_ok = intent == exp_intent
    group_ok = group == exp_group
    ok = conv_ok and intent_ok and group_ok
    status = "PASS" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  {text:25s} conv={str(prof.is_conversational):5s} intent={str(intent):5s} group={group:12s} [{status}]")

print()
print("Overall: " + ("ALL PASS" if all_pass else "SOME FAIL"))