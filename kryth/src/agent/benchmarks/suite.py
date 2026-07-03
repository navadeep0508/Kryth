"""KRYTH Benchmark Suite — 50 prompts across 6 categories.

Usage:
    python -m agent.benchmarks.suite              # run all 50
    python -m agent.benchmarks.suite --category READ  # run one category
    python -m agent.benchmarks.suite --list          # list prompts

Metrics tracked: pass_rate, avg_tokens, avg_latency, tool_calls,
                 loop_count, duplicate_reads, crash_rate.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable, Optional


# ── Bootstrap path ────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent.parent  # kryth/src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ── Metrics ───────────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    task: str = ""
    category: str = ""
    status: str = ""          # done | crashed | api_error | max_turns | interrupted
    latency_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    turns_used: int = 0
    finish_reason: str = ""
    loop_count: int = 0
    duplicate_reads: int = 0
    error: str = ""
    crash: bool = False


@dataclass
class BenchmarkReport:
    total: int = 0
    passed: int = 0
    failed: int = 0
    crashed: int = 0
    success_rate: float = 0.0
    avg_latency_s: float = 0.0
    avg_tokens: float = 0.0
    avg_tool_calls: float = 0.0
    avg_turns: float = 0.0
    avg_loop_count: float = 0.0
    avg_duplicate_reads: float = 0.0
    crash_rate: float = 0.0
    by_category: dict = field(default_factory=dict)
    results: list = field(default_factory=list)


# ── Prompt definition ─────────────────────────────────────────────────────

@dataclass
class Prompt:
    name: str
    category: str
    prompt: str
    setup: Optional[Callable] = None
    teardown: Optional[Callable] = None
    expected_status: str = "done"


# ── Prompt catalog — 50 prompts ───────────────────────────────────────────
# Each prompt has a setup function that creates a temp project, and a
# teardown that cleans up. The runner calls setup → agent → teardown.

_CWD_BACKUP: str = ""
_TEMP_DIR: str = ""


def _setup_temp():
    global _CWD_BACKUP, _TEMP_DIR
    _CWD_BACKUP = os.getcwd()
    _TEMP_DIR = tempfile.mkdtemp()
    os.chdir(_TEMP_DIR)


def _teardown_temp():
    global _CWD_BACKUP, _TEMP_DIR
    os.chdir(_CWD_BACKUP)
    import shutil
    shutil.rmtree(_TEMP_DIR, ignore_errors=True)
    _TEMP_DIR = ""


def _write(path: str, content: str):
    p = Path(_TEMP_DIR) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _noop():
    pass


# ── Setup factories ───────────────────────────────────────────────────────

def _setup_read_project():
    _setup_temp()
    _write("README.md", "# My Project\n\nA Flask-based translation API.\n\n## Setup\n```\npip install -r requirements.txt\n```")
    _write("app.py", """from flask import Flask, request, jsonify
from flask_cors import CORS
app = Flask(__name__)
CORS(app)

LANGUAGES = {'en': 'English', 'es': 'Spanish', 'fr': 'French'}

@app.route('/api/languages', methods=['GET'])
def get_languages():
    return jsonify(LANGUAGES)

@app.route('/api/translate', methods=['POST'])
def translate():
    data = request.get_json()
    text = data.get('text', '')
    src = data.get('source', 'auto')
    tgt = data.get('target', 'en')
    return jsonify({'translated': f'[{src}→{tgt}] {text}'})

@app.route('/api/health')
def health():
    return jsonify({'status': 'healthy'})
""")
    _write("static/script.js", "// frontend logic\nasync function translate() {\n  const res = await fetch('/api/translate', {method:'POST', body: JSON.stringify({text: input.value})});\n  return res.json();\n}")
    _write("tests/test_app.py", "def test_health():\n    assert 1 + 1 == 2")


def _setup_simple_flask():
    _setup_temp()
    _write("app.py", """from flask import Flask, jsonify
app = Flask(__name__)

@app.route('/')
def home():
    return 'Hello World'

@app.route('/api/data')
def data():
    return {'items': [1, 2, 3]}

@app.route('/api/health')
def health():
    return {'status': 'ok'}
""")


def _setup_buggy_code():
    _setup_temp()
    _write("buggy.py", """def greet(name):
    print("Hello " + name)

def add(a, b)
    return a + b

def divide(a, b):
    return a / b

class Car:
    def __init__(self, make, model):
        self.make = make
        self.model = model

    def display_info()
        print(f"{self.make} {self.model}")
""")


def _setup_auth_project():
    _setup_temp()
    _write("auth.py", """from flask import Flask, request, jsonify
from functools import wraps

app = Flask(__name__)

API_KEY = "supersecret"

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get('X-API-Key')
        if key != API_KEY:
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

@app.route('/api/public')
def public():
    return jsonify({'data': 'public'})

@app.route('/api/secret')
@require_auth
def secret():
    return jsonify({'data': 'classified'})
""")


def _setup_multi_file():
    _setup_temp()
    _write("src/main.py", "def main():\n    print('running')\n\nif __name__ == '__main__':\n    main()")
    _write("src/models.py", "class User:\n    def __init__(self, name):\n        self.name = name\n\ndef format_name(name):\n    return name.strip().title()")
    _write("src/utils.py", "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b")


def _setup_run_project():
    _setup_temp()
    _write("hello.py", "print('hello from benchmark')")


def _setup_tests():
    _setup_temp()
    _write("test_math.py", """def test_add():
    assert 1 + 2 == 3

def test_subtract():
    assert 5 - 3 == 2

def test_multiply():
    assert 2 * 3 == 6

def test_fail():
    assert 1 == 2
""")


def _setup_empty_project():
    _setup_temp()
    _write("README.md", "# Empty Project\n\nNothing here yet.")


# ── 50 prompts ────────────────────────────────────────────────────────────

PROMPTS: list[Prompt] = [
    # ── CHAT (5) ──────────────────────────────────────────────────────────
    Prompt("hi", "CHAT", "hi", _noop, _noop),
    Prompt("hello", "CHAT", "hello", _noop, _noop),
    Prompt("what_are_you", "CHAT", "what are you", _noop, _noop),
    Prompt("who_made_you", "CHAT", "who made you", _noop, _noop),
    Prompt("how_are_you", "CHAT", "how are you", _noop, _noop),

    # ── READ (15) ─────────────────────────────────────────────────────────
    Prompt("read_project", "READ", "read this project", _setup_read_project, _teardown_temp),
    Prompt("explain_app", "READ", "explain what app.py does", _setup_simple_flask, _teardown_temp),
    Prompt("summarize_repo", "READ", "summarize the project structure", _setup_read_project, _teardown_temp),
    Prompt("list_files", "READ", "list all files", _setup_read_project, _teardown_temp),
    Prompt("find_routes", "READ", "find all routes in the project", _setup_simple_flask, _teardown_temp),
    Prompt("trace_auth", "READ", "trace the authentication flow", _setup_auth_project, _teardown_temp),
    Prompt("find_endpoints", "READ", "find all API endpoints", _setup_read_project, _teardown_temp),
    Prompt("read_readme", "READ", "read the README", _setup_read_project, _teardown_temp),
    Prompt("list_deps", "READ", "list all dependencies", _setup_read_project, _teardown_temp),
    Prompt("describe_architecture", "READ", "describe the architecture", _setup_multi_file, _teardown_temp),
    Prompt("find_main_file", "READ", "find the main entry point", _setup_multi_file, _teardown_temp),
    Prompt("what_models", "READ", "what data models exist", _setup_multi_file, _teardown_temp),
    Prompt("how_translate_works", "READ", "how does the translation work", _setup_read_project, _teardown_temp),
    Prompt("read_scriptjs", "READ", "read static/script.js and summarize", _setup_read_project, _teardown_temp),
    Prompt("check_test_coverage", "READ", "check what tests exist", _setup_read_project, _teardown_temp),

    # ── SEARCH (6) ────────────────────────────────────────────────────────
    Prompt("find_todos", "SEARCH", "find all TODO comments", _setup_read_project, _teardown_temp),
    Prompt("search_error_handling", "SEARCH", "find error handling patterns", _setup_simple_flask, _teardown_temp),
    Prompt("find_api_routes", "SEARCH", "search for route definitions", _setup_read_project, _teardown_temp),
    Prompt("find_auth_code", "SEARCH", "find authentication code", _setup_auth_project, _teardown_temp),
    Prompt("search_decorators", "SEARCH", "find all decorators", _setup_auth_project, _teardown_temp),
    Prompt("find_imports", "SEARCH", "find all import statements", _setup_multi_file, _teardown_temp),

    # ── MODIFY (12) ──────────────────────────────────────────────────────
    Prompt("fix_syntax", "MODIFY", "fix the syntax errors in buggy.py", _setup_buggy_code, _teardown_temp),
    Prompt("add_endpoint", "MODIFY", "add a new route /api/status that returns OK", _setup_simple_flask, _teardown_temp),
    Prompt("rename_function", "MODIFY", "rename the add function to calculate_sum in utils.py", _setup_multi_file, _teardown_temp),
    Prompt("add_error_handling", "MODIFY", "add try/except error handling to the divide function", _setup_buggy_code, _teardown_temp),
    Prompt("update_readme", "MODIFY", "update the README to mention the new API endpoint", _setup_read_project, _teardown_temp),
    Prompt("add_logging", "MODIFY", "add logging to the translate function", _setup_read_project, _teardown_temp),
    Prompt("fix_tests", "MODIFY", "fix the failing test in test_math.py", _setup_tests, _teardown_temp),
    Prompt("add_type_hints", "MODIFY", "add type hints to all functions", _setup_multi_file, _teardown_temp),
    Prompt("refactor_duplicate", "MODIFY", "extract the auth check into a reusable decorator", _setup_auth_project, _teardown_temp),
    Prompt("change_response_format", "MODIFY", "change all API responses to include version field", _setup_simple_flask, _teardown_temp),
    Prompt("add_validation", "MODIFY", "add input validation to the translate endpoint", _setup_read_project, _teardown_temp),
    Prompt("rename_route", "MODIFY", "rename /api/data to /api/items", _setup_simple_flask, _teardown_temp),

    # ── RUN (7) ───────────────────────────────────────────────────────────
    Prompt("run_hello", "RUN", "run hello.py", _setup_run_project, _teardown_temp),
    Prompt("run_tests", "RUN", "run all tests", _setup_tests, _teardown_temp),
    Prompt("install_deps", "RUN", "install dependencies from requirements.txt", _setup_read_project, _teardown_temp),
    Prompt("check_syntax", "RUN", "check syntax of all Python files", _setup_buggy_code, _teardown_temp),
    Prompt("run_lint", "RUN", "run flake8 on the project", _setup_read_project, _teardown_temp),
    Prompt("format_code", "RUN", "format all Python files with black", _setup_multi_file, _teardown_temp),
    Prompt("check_imports", "RUN", "check for unused imports", _setup_multi_file, _teardown_temp),

    # ── BUILD (5) ─────────────────────────────────────────────────────────
    Prompt("create_flask_app", "BUILD", "create a flask app with a single /hello route", _setup_empty_project, _teardown_temp),
    Prompt("build_cli_tool", "BUILD", "create a CLI tool that greets the user", _setup_empty_project, _teardown_temp),
    Prompt("create_rest_api", "BUILD", "create a REST API with CRUD endpoints", _setup_empty_project, _teardown_temp),
    Prompt("build_landing_page", "BUILD", "create a landing page with HTML and CSS", _setup_empty_project, _teardown_temp),
    Prompt("create_todo_app", "BUILD", "create a todo app with Flask and a JSON file backend", _setup_empty_project, _teardown_temp),
]

# Shuffle categories evenly in the canonical run order (CHAT, READ, SEARCH, MODIFY, RUN, BUILD)
# The list above is already in that order.

CATEGORY_ORDER = ["CHAT", "READ", "SEARCH", "MODIFY", "RUN", "BUILD"]


def get_prompts(category: str = "") -> list[Prompt]:
    if not category:
        return PROMPTS
    return [p for p in PROMPTS if p.category == category]


# ── Runner ────────────────────────────────────────────────────────────────

def run_single(prompt: Prompt) -> BenchmarkResult:
    """Run one benchmark prompt through the agent and collect metrics."""
    result = BenchmarkResult(task=prompt.name, category=prompt.category)

    prompt.setup()
    start = time.monotonic()

    try:
        from agent.agent_loop import run_agent, LoopResult
        from agent.runtime.scratchpad import scratch as _bm_scratch
        from agent.session import get_session

        session = get_session()
        session.reset()

        _result = run_agent(prompt.prompt)
        latency = time.monotonic() - start

        result.latency_s = round(latency, 2)
        result.status = getattr(_result, "status", "unknown")
        result.turns_used = getattr(_result, "turns_used", 0)
        result.finish_reason = getattr(_result, "finish_reason", "")

        # Token usage from session
        result.tokens_in = getattr(session, "cumulative_in_tokens", 0)
        result.tokens_out = getattr(session, "cumulative_out_tokens", 0)

        # Tool call count from session
        result.tool_calls = getattr(session, "tool_call_count", 0)

        # Scratchpad metrics
        try:
            if _bm_scratch.state is not None:
                result.loop_count = getattr(_bm_scratch.state, "_loop_count", 0)
                result.duplicate_reads = sum(
                    1 for s in getattr(_bm_scratch.state, "completed_steps", [])
                    if s.startswith("read ")
                )
        except Exception:
            pass

    except Exception as e:
        result.latency_s = round(time.monotonic() - start, 2)
        result.status = "crashed"
        result.crash = True
        result.error = f"{type(e).__name__}: {e}"
    finally:
        prompt.teardown()

    return result


def run_benchmarks(category: str = "", verbose: bool = True) -> BenchmarkReport:
    """Run all (or filtered) prompts and return a report."""
    prompts = get_prompts(category)

    if not prompts:
        print(f"No prompts found for category '{category}'")
        return BenchmarkReport()

    results: list[BenchmarkResult] = []
    report = BenchmarkReport()

    if verbose:
        print(f"\n{'='*70}")
        print(f"  KRYTH BENCHMARK SUITE  —  {len(prompts)} prompts")
        if category:
            print(f"  Category: {category}")
        print(f"{'='*70}")

    for i, prompt in enumerate(prompts, 1):
        label = f"  [{i}/{len(prompts)}] {prompt.category:8s} {prompt.name:25s}"
        if verbose:
            print(f"{label} ", end="", flush=True)

        r = run_single(prompt)

        if verbose:
            if r.crash:
                print(f"  CRASH ({r.latency_s:.1f}s)")
            elif r.status == "done":
                print(f"  PASS ({r.latency_s:.1f}s, {r.turns_used}turns, {r.tool_calls}tools)")
            else:
                print(f"  FAIL ({r.status}, {r.latency_s:.1f}s)")

        results.append(r)

    # ── Aggregate ────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.status == "done" and not r.crash)
    failed = sum(1 for r in results if r.status not in ("done", "") and not r.crash)
    crashed = sum(1 for r in results if r.crash)
    total = len(results)

    report.total = total
    report.passed = passed
    report.failed = failed
    report.crashed = crashed
    report.success_rate = round(passed / total * 100, 1) if total else 0.0
    report.crash_rate = round(crashed / total * 100, 1) if total else 0.0

    passed_results = [r for r in results if r.status == "done" and not r.crash]
    if passed_results:
        report.avg_latency_s = round(sum(r.latency_s for r in passed_results) / len(passed_results), 2)
        report.avg_tokens = round(sum(r.tokens_in + r.tokens_out for r in passed_results) / len(passed_results), 1)
        report.avg_tool_calls = round(sum(r.tool_calls for r in passed_results) / len(passed_results), 1)
        report.avg_turns = round(sum(r.turns_used for r in passed_results) / len(passed_results), 1)
        report.avg_loop_count = round(sum(r.loop_count for r in passed_results) / len(passed_results), 2)
        report.avg_duplicate_reads = round(sum(r.duplicate_reads for r in passed_results) / len(passed_results), 1)

    # By category
    for cat in CATEGORY_ORDER:
        cat_results = [r for r in results if r.category == cat]
        if cat_results:
            cat_passed = sum(1 for r in cat_results if r.status == "done" and not r.crash)
            cat_total = len(cat_results)
            report.by_category[cat] = {
                "total": cat_total,
                "passed": cat_passed,
                "success_rate": round(cat_passed / cat_total * 100, 1) if cat_total else 0.0,
                "avg_latency_s": round(sum(r.latency_s for r in cat_results if r.status == "done") / max(cat_passed, 1), 2),
            }

    report.results = [asdict(r) for r in results]
    return report


# ── Report output ─────────────────────────────────────────────────────────

def print_report(report: BenchmarkReport):
    print(f"\n{'='*70}")
    print(f"  BENCHMARK RESULTS")
    print(f"{'='*70}")
    print(f"  Total prompts:  {report.total}")
    print(f"  Passed:         {report.passed}")
    print(f"  Failed:         {report.failed}")
    print(f"  Crashed:        {report.crashed}")
    print(f"  Success rate:   {report.success_rate}%")
    print(f"  Crash rate:     {report.crash_rate}%")
    print()
    print(f"  Avg latency:    {report.avg_latency_s}s")
    print(f"  Avg tokens:     {report.avg_tokens}")
    print(f"  Avg tool calls: {report.avg_tool_calls}")
    print(f"  Avg turns:      {report.avg_turns}")
    print(f"  Avg loop count: {report.avg_loop_count}")
    print(f"  Avg dup reads:  {report.avg_duplicate_reads}")

    print(f"\n  {'─'*50}")
    print(f"  BY CATEGORY")
    print(f"  {'─'*50}")
    for cat in CATEGORY_ORDER:
        if cat in report.by_category:
            c = report.by_category[cat]
            bar = "█" * int(c["success_rate"] / 10) + "░" * (10 - int(c["success_rate"] / 10))
            print(f"  {cat:8s} {bar} {c['passed']}/{c['total']}  ({c['success_rate']}%)  avg {c['avg_latency_s']}s")

    # Scorecard
    print(f"\n  {'─'*50}")
    print(f"  SCORECARD")
    print(f"  {'─'*50}")

    checks = [
        ("Success rate >= 90%", report.success_rate >= 90.0, f"{report.success_rate}%"),
        ("Crash rate < 5%", report.crash_rate < 5.0, f"{report.crash_rate}%"),
        ("Avg turns < 6", report.avg_turns < 6, f"{report.avg_turns}"),
        ("Avg loop count < 1", report.avg_loop_count < 1.0, f"{report.avg_loop_count}"),
        ("Avg dup reads < 2", report.avg_duplicate_reads < 2.0, f"{report.avg_duplicate_reads}"),
        ("Avg tool calls < 15", report.avg_tool_calls < 15, f"{report.avg_tool_calls}"),
    ]
    for label, passed_val, value in checks:
        icon = "✅" if passed_val else "❌"
        print(f"  {icon} {label:30s} {value}")

    print(f"\n{'='*70}")


def save_report(report: BenchmarkReport, path: str = ""):
    if not path:
        path = str(_HERE / "benchmark_report.json")
    data = asdict(report)
    Path(path).write_text(json.dumps(data, indent=2))
    print(f"\n  Report saved to {path}")


# ── CLI ───────────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="KRYTH Benchmark Suite")
    parser.add_argument("--category", "-c", default="", help="Filter by category (CHAT, READ, SEARCH, MODIFY, RUN, BUILD)")
    parser.add_argument("--list", "-l", action="store_true", help="List prompts without running")
    parser.add_argument("--save", "-s", default="", help="Save report to path")
    parser.add_argument("--verbose", "-v", action="store_true", default=True, help="Verbose output")

    args = parser.parse_args()

    if args.list:
        prompts = get_prompts(args.category)
        print(f"\nPrompts ({len(prompts)}):")
        for p in prompts:
            print(f"  [{p.category:8s}] {p.name:30s} {p.prompt}")
        return

    report = run_benchmarks(category=args.category, verbose=args.verbose)
    print_report(report)

    save_path = args.save or str(_HERE / "benchmark_report.json")
    save_report(report, save_path)

    return 0 if report.failed == 0 and report.crashed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
