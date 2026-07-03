import os, sys, shutil, tempfile, json, time

# Set env vars BEFORE importing live_validation so it picks them up
os.environ['KRYTH_ASSUME_YES'] = '1'
os.environ['KRYTH_LIVE_PROMPT_TIMEOUT'] = '600'
os.environ['KRYTH_MAIN_MODEL'] = 'stepfun-ai/step-3.5-flash'
os.environ['KRYTH_PLANNER_MODEL'] = 'stepfun-ai/step-3.5-flash'

# Add kryth directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'kryth'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'kryth', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'kryth', 'tests'))

# Remove cached module to force re-import
if 'tests.live_validation' in sys.modules:
    del sys.modules['tests.live_validation']

from tests.live_validation import _bootstrap_env, run_prompt, _has_key
_bootstrap_env()

BASE = 'test_workflow'
PROJECTS = [
    ('01-expense-tracker', 'Build an expense tracker with React + Tailwind frontend (Vite), FastAPI backend, SQLite. Add/edit/delete expenses, monthly charts, category filters, CSV export.'),
    ('02-resume-analyzer', 'Build an AI resume analyzer with PDF upload, skill extraction using NLP, ATS scoring, React frontend.'),
    ('03-url-shortener', 'Build a URL shortener with custom aliases, click analytics, QR codes, rate limiting. FastAPI + React.'),
    ('04-notes-app', 'Build a Notion-like notes app with markdown editor, nested pages, autosave. FastAPI + React.'),
]

if not _has_key():
    print('FAIL: No API key')
    sys.exit(1)

for pid, prompt in PROJECTS:
    d = os.path.join(BASE, pid)
    os.makedirs(d, exist_ok=True)
    print(f'BUILDING {pid}...')
    r = run_prompt(prompt, d)
    created = [f for f in r.get('created', []) if not f.startswith('__pycache__') and 'graphify' not in f]
    print(f'  Status: {r["status"]}, Tools: {len(r["tools"])}, Files: {len(created)}')
    if r.get('error'):
        print(f'  Error: {r["error"]}')
    if r.get('status') is None:
        print(f'  DEBUG: full result = {r}')
    print()