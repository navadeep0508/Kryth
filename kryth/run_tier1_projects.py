"""Run all 4 Tier 1 projects through KRYTH agent and verify results."""
import os, sys, shutil, tempfile, json, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'kryth', 'src'))
os.chdir(os.path.dirname(__file__))

from tests.live_validation import _bootstrap_env, run_prompt, _has_key
_bootstrap_env()

os.environ['KRYTH_MAIN_MODEL'] = 'stepfun-ai/step-3.5-flash'
os.environ['KRYTH_PLANNER_MODEL'] = 'stepfun-ai/step-3.5-flash'
os.environ['KRYTH_ASSUME_YES'] = '1'
os.environ['KRYTH_LIVE_PROMPT_TIMEOUT'] = '180'

BASE = os.path.join(os.path.dirname(__file__), 'test_workflow')

PROJECTS = [
    {
        "id": "01-expense-tracker",
        "prompt": "Build an expense tracker web app with React + Tailwind frontend (Vite), FastAPI backend, SQLite database. Features: add/edit/delete expenses, monthly charts by category, category filters, CSV export. Create all files and make it runnable.",
    },
    {
        "id": "02-resume-analyzer",
        "prompt": "Build an AI resume analyzer web app with React + Tailwind frontend (Vite) and FastAPI backend. Users upload resumes (PDF) and get: skill extraction using NLP, ATS score, missing keywords. Use Python NLP libraries. Create all files and make it runnable.",
    },
    {
        "id": "03-url-shortener",
        "prompt": "Build a production-ready URL shortener web app with React + Tailwind frontend (Vite) and FastAPI backend. Features: custom aliases for shortened URLs, click analytics, QR code generation, rate limiting middleware. Use SQLAlchemy + SQLite for storage. Create all files and make it runnable.",
    },
    {
        "id": "04-notes-app",
        "prompt": "Build a Notion-like notes web app with React + Tailwind frontend (Vite) and FastAPI backend. Features: markdown editor (with preview), nested pages (parent/child hierarchy with tree sidebar), autosave (debounced). Use SQLAlchemy + SQLite. Create all files and make it runnable.",
    },
]


def _check_project(pid, d, r):
    fails = []
    # Check files were created
    if not any(f.endswith('.py') for root, dirs, files in os.walk(d) for f in files):
        fails.append("no Python files created")
    if not r.get('tools'):
        fails.append("no tools used")
    if r.get('error'):
        fails.append(f"error: {r['error']}")
    # Count meaningful created files (exclude cache artifacts)
    created = [f for f in r.get('created', []) if not f.startswith('__pycache__') and 'graphify' not in f]
    if len(created) < 2:
        fails.append(f"too few files created ({len(created)}): {created}")
    return fails


if not _has_key():
    print("FAIL: No API key configured")
    sys.exit(1)

results = []
for proj in PROJECTS:
    pid = proj["id"]
    d = os.path.join(BASE, pid)
    os.makedirs(d, exist_ok=True)
    print(f"\n{'='*60}")
    print(f"BUILDING {pid}...")
    print(f"{'='*60}")

    t0 = time.monotonic()
    r = run_prompt(proj["prompt"], d)
    elapsed = time.monotonic() - t0

    created_clean = [f for f in r.get('created', []) if not f.startswith('__pycache__') and 'graphify' not in f]

    print(f"  Status: {r['status']}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Tools: {r['tools']}")
    print(f"  Files created: {created_clean[:15]}")
    if r.get('error'):
        print(f"  Error: {r['error']}")

    fails = _check_project(pid, d, r)
    if fails:
        print(f"  FAILED: {'; '.join(fails)}")
    else:
        print(f"  PASS")

    results.append((pid, r, elapsed, fails))

print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
all_ok = True
for pid, r, elapsed, fails in results:
    status = "PASS" if not fails else "FAIL"
    if fails:
        all_ok = False
    created = len([f for f in r.get('created', []) if not f.startswith('__pycache__') and 'graphify' not in f])
    print(f"  [{status}] {pid} ({elapsed:.1f}s) tools={len(r.get('tools', []))} files={created} status={r['status']}")
    for f in fails:
        print(f"         -> {f}")

if all_ok:
    print("\nALL 4 PROJECTS PASSED")
else:
    print(f"\nSOME PROJECTS FAILED")
    sys.exit(1)
