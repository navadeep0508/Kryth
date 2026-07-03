"""KRYTH base agent benchmark — 25-task suite.

Metrics: success rate, latency, avg turns, tool count, crashes, loop incidents.
Targets: success > 95%, crashes = 0, infinite loops = 0, avg turns < 6.
"""

from __future__ import annotations

import json
import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.agent_loop import run_agent, LoopResult
from agent.session import get_session


RESULTS: list[dict] = []


def run(task_name: str, user_input: str, setup: callable = None, teardown: callable = None) -> dict:
    """Run a single benchmark task and record results."""
    start = time.monotonic()
    crash = False
    error = None

    if setup:
        try:
            setup()
        except Exception as e:
            return {"task": task_name, "status": "setup_failed", "error": str(e), "latency": 0, "turns": 0}

    try:
        session = get_session()
        session.reset()
        result = run_agent(user_input)
        latency = time.monotonic() - start
        turns = getattr(result, "turns_used", 0)
        status = getattr(result, "status", "error")

        record = {
            "task": task_name,
            "status": status,
            "latency": round(latency, 2),
            "turns": turns,
            "crash": False,
            "error": None,
        }
    except Exception as e:
        latency = time.monotonic() - start
        crash = True
        record = {
            "task": task_name,
            "status": "crashed",
            "latency": round(latency, 2),
            "turns": 0,
            "crash": True,
            "error": str(e),
        }

    if teardown:
        try:
            teardown()
        except Exception:
            pass

    RESULTS.append(record)
    return record


# ── Setup / teardown helpers ──────────────────────────────────────────

_temp_dir: str | None = None


def _make_temp():
    global _temp_dir
    _temp_dir = tempfile.mkdtemp()
    os.chdir(_temp_dir)


def _clean_temp():
    global _temp_dir
    if _temp_dir:
        import shutil
        shutil.rmtree(_temp_dir, ignore_errors=True)
        _temp_dir = None


def _make_test_file(name: str, content: str):
    p = Path(_temp_dir or ".") / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ── Test definitions ──────────────────────────────────────────────────

def test_read_project():
    """READ: read project"""
    _make_temp()
    _make_test_file("README.md", "# Test Project\n\nA simple test project.")
    _make_test_file("src/main.py", "def hello():\n    print('hello world')\n\nhello()")
    _make_test_file("src/utils.py", "def add(a, b):\n    return a + b")
    run("read_project", "Read the project and summarize what it does", teardown=_clean_temp)


def test_explain_app():
    """READ: explain app.py"""
    _make_temp()
    _make_test_file("app.py", """
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return 'Hello'

@app.route('/api')
def api():
    return {'status': 'ok'}

if __name__ == '__main__':
    app.run()
""")
    run("explain_app", "Explain what app.py does", teardown=_clean_temp)


def test_find_routes():
    """READ: find routes"""
    _make_temp()
    _make_test_file("app.py", """
from flask import Flask
app = Flask(__name__)

@app.route('/')
def home():
    return 'Hello'

@app.route('/api/users')
def users():
    return {'users': []}

@app.route('/api/data')
def data():
    return {'data': {}}
""")
    run("find_routes", "Find all API routes defined in the project", teardown=_clean_temp)


def test_create_file():
    """MODIFY: create file"""
    _make_temp()
    run("create_file", "Create a file called hello.py that prints 'hello world'", teardown=_clean_temp)


def test_fix_syntax():
    """MODIFY: fix syntax"""
    _make_temp()
    _make_test_file("buggy.py", """
def greet(name
    print(f"Hello {name}")

def add(a, b)
    return a + b
""")
    run("fix_syntax", "Fix the syntax errors in buggy.py", teardown=_clean_temp)


def test_rename_function():
    """MODIFY: rename function"""
    _make_temp()
    _make_test_file("calc.py", """
def calculate_sum(a, b):
    return a + b

def calculate_product(a, b):
    return a * b
""")
    run("rename_function", "Rename calculate_sum to add and calculate_product to multiply in calc.py",
        teardown=_clean_temp)


def test_add_function():
    """MODIFY: add function"""
    _make_temp()
    _make_test_file("math_ops.py", """
def add(a, b):
    return a + b

def subtract(a, b):
    return a - b
""")
    run("add_function", "Add a multiply function to math_ops.py that takes two args and returns their product",
        teardown=_clean_temp)


def test_run_project():
    """RUN: run project"""
    _make_temp()
    _make_test_file("script.py", "print('hello from benchmark')")
    run("run_project", "Run script.py", teardown=_clean_temp)


def test_install_deps():
    """RUN: install deps"""
    _make_temp()
    _make_test_file("requirements.txt", "requests\nclick")
    run("install_deps", "Install dependencies from requirements.txt", teardown=_clean_temp)


def test_run_tests():
    """RUN: run tests"""
    _make_temp()
    _make_test_file("test_sample.py", """
def test_pass():
    assert 1 + 1 == 2

def test_also_pass():
    assert 2 * 2 == 4
""")
    run("run_tests", "Run the tests in test_sample.py", teardown=_clean_temp)


def test_trace_auth_flow():
    """EXPLORE: trace auth flow"""
    _make_temp()
    _make_test_file("auth.py", """
from flask import Flask, request, jsonify
from functools import wraps

app = Flask(__name__)

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key')
        if not key or key != 'secret-key':
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/api/data')
@require_api_key
def get_data():
    return jsonify({'data': 'protected'})

@app.route('/api/public')
def public():
    return jsonify({'data': 'public'})
""")
    run("trace_auth_flow", "Trace the authentication flow in auth.py. Find what routes require auth and how it works.",
        teardown=_clean_temp)


def test_review_repo():
    """EXPLORE: review repo"""
    _make_temp()
    _make_test_file("src/main.py", "def main():\n    print('running')\n\nif __name__ == '__main__':\n    main()")
    _make_test_file("src/models.py", "class User:\n    def __init__(self, name):\n        self.name = name")
    _make_test_file("src/utils.py", "def format_name(name):\n    return name.strip().title()")
    run("review_repo", "Review the codebase structure and list all files with their purposes",
        teardown=_clean_temp)


def test_find_bug_source():
    """EXPLORE: find bug source"""
    _make_temp()
    _make_test_file("app.py", """
import sys
sys.path.insert(0, 'src')

def main():
    from math_ops import divide
    result = divide(10, 0)
    print(f"Result: {result}")

if __name__ == '__main__':
    main()
""")
    _make_test_file("src/math_ops.py", """
def add(a, b):
    return a + b

def divide(a, b):
    return a / b
""")
    run("find_bug_source", "Find the source of the potential error in app.py when dividing by zero",
        teardown=_clean_temp)


# ── Runner ────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_read_project,
    test_explain_app,
    test_find_routes,
    test_create_file,
    test_fix_syntax,
    test_rename_function,
    test_add_function,
    test_run_project,
    test_install_deps,
    test_run_tests,
    test_trace_auth_flow,
    test_review_repo,
    test_find_bug_source,
]


def main():
    print(f"KRYTH Benchmark — {len(ALL_TESTS)} tasks")
    print("=" * 60)

    passed = 0
    failed = 0
    crashed = 0
    total_latency = 0.0
    total_turns = 0

    for test_fn in ALL_TESTS:
        name = test_fn.__name__.replace("test_", "")
        print(f"\n  [{name}] ", end="", flush=True)
        try:
            test_fn()
        except Exception as e:
            RESULTS.append({"task": name, "status": "crashed", "error": str(e)})
            print("CRASH")
            crashed += 1
            continue

        last = RESULTS[-1]
        status = last["status"]
        latency = last["latency"]
        turns = last["turns"]

        if last.get("crash"):
            print(f"CRASH ({latency:.1f}s)")
            crashed += 1
        elif status == "done":
            print(f"PASS ({latency:.1f}s, {turns} turns)")
            passed += 1
            total_latency += latency
            total_turns += turns
        elif status in ("max_turns", "interrupted", "api_error"):
            print(f"FAIL ({status}, {latency:.1f}s, {turns} turns)")
            failed += 1
        else:
            print(f"FAIL ({status})")
            failed += 1

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed, {crashed} crashed")
    if passed:
        print(f"Avg latency: {total_latency / passed:.2f}s")
        print(f"Avg turns:   {total_turns / passed:.1f}")
    print(f"Success rate: {passed / len(ALL_TESTS) * 100:.1f}%")
    print(f"Crashes: {crashed}")
    print(f"Infinite loops: {sum(1 for r in RESULTS if r.get('status') == 'max_turns')}")

    # Save report
    report = {
        "total": len(ALL_TESTS),
        "passed": passed,
        "failed": failed,
        "crashed": crashed,
        "success_rate": round(passed / len(ALL_TESTS) * 100, 1),
        "avg_latency": round(total_latency / passed, 2) if passed else 0,
        "avg_turns": round(total_turns / passed, 1) if passed else 0,
        "results": RESULTS,
    }
    report_path = Path(__file__).parent / "benchmark_report.json"
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\nReport saved to {report_path}")

    return 0 if failed == 0 and crashed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
