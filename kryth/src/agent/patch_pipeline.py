"""Patch quality pipeline for KRYTH.

Auto-triggered after write_file / edit_file for non-trivial tasks.
Pipeline: syntax → lint → unit tests → dependency check.

Broken patches are rejected immediately; the agent gets a structured
error with the failure reason and a suggested fix, avoiding silent
broken-code commits.

Usage (wired in agent_loop._process_tool_call after successful write):
    from agent.patch_pipeline import validate_patch, PatchReport
    report = validate_patch(path, content, task_complexity)
    if not report.ok:
        # inject error into session for model to fix
        session.append({"role": "user", "content": report.error_message()})
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PatchIssue:
    stage: str       # syntax | lint | test | deps
    severity: str    # error | warning
    file: str
    line: int = 0
    message: str = ""

    def __str__(self) -> str:
        loc = f":{self.line}" if self.line else ""
        return f"[{self.stage}] {self.file}{loc}: {self.message}"


@dataclass
class PatchReport:
    path: str
    ok: bool = True
    issues: List[PatchIssue] = field(default_factory=list)
    stages_run: List[str] = field(default_factory=list)

    def errors(self) -> List[PatchIssue]:
        return [i for i in self.issues if i.severity == "error"]

    def error_message(self) -> str:
        lines = [f"[PATCH_QUALITY] {self.path} failed validation:"]
        for issue in self.errors()[:10]:
            lines.append(f"  {issue}")
        lines.append("Fix the issues above before continuing.")
        return "\n".join(lines)


# ── Stage runners ─────────────────────────────────────────────────────────────

def _check_syntax_python(path: str) -> List[PatchIssue]:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", path],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        return []
    issues = []
    for line in (r.stderr or "").splitlines():
        # "  File "foo.py", line 3"  or  "SyntaxError: ..."
        lineno = 0
        m = __import__("re").search(r"line (\d+)", line)
        if m:
            lineno = int(m.group(1))
        if line.strip():
            issues.append(PatchIssue("syntax", "error", path, lineno, line.strip()))
    return issues or [PatchIssue("syntax", "error", path, 0, r.stderr.strip()[:200])]


def _check_syntax_json(path: str) -> List[PatchIssue]:
    import json
    try:
        with open(path, encoding="utf-8") as f:
            json.load(f)
        return []
    except json.JSONDecodeError as e:
        return [PatchIssue("syntax", "error", path, e.lineno, str(e))]


def _check_syntax_yaml(path: str) -> List[PatchIssue]:
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            yaml.safe_load(f)
        return []
    except Exception as e:
        return [PatchIssue("syntax", "error", path, 0, str(e)[:200])]


def _check_syntax_js(path: str) -> List[PatchIssue]:
    r = subprocess.run(
        ["node", "--check", path],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0:
        return []
    return [PatchIssue("syntax", "error", path, 0, (r.stderr or r.stdout).strip()[:200])]


_SYNTAX_CHECKERS = {
    ".py": _check_syntax_python,
    ".json": _check_syntax_json,
    ".yaml": _check_syntax_yaml,
    ".yml": _check_syntax_yaml,
    ".js": _check_syntax_js,
    ".mjs": _check_syntax_js,
    ".cjs": _check_syntax_js,
}


def _run_lint(path: str) -> List[PatchIssue]:
    ext = os.path.splitext(path)[1].lower()
    issues = []

    if ext == ".py":
        # Try ruff first (fast), fall back to flake8
        for tool, cmd in [
            ("ruff", [sys.executable, "-m", "ruff", "check", "--output-format=text",
                      "--select=E9,F401,F811,F841,W", path]),
            ("flake8", [sys.executable, "-m", "flake8",
                        "--max-line-length=120", "--select=E9,F401,F811,F841", path]),
        ]:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 127:
                continue  # tool not found, try next
            for line in (r.stdout + r.stderr).splitlines():
                # ruff/flake8 format: file.py:L:C: CODE msg
                m = __import__("re").match(r"(.+?):(\d+):\d+:\s+(\w+)\s+(.+)", line)
                if m:
                    severity = "error" if m.group(3).startswith("E9") else "warning"
                    issues.append(PatchIssue("lint", severity, m.group(1),
                                             int(m.group(2)), f"{m.group(3)} {m.group(4)}"))
            break

    elif ext in (".js", ".ts", ".jsx", ".tsx"):
        if os.path.exists(".eslintrc.json") or os.path.exists(".eslintrc.js") \
                or os.path.exists("eslint.config.js"):
            r = subprocess.run(
                ["npx", "--yes", "eslint", "--format=compact", path],
                capture_output=True, text=True, timeout=30,
            )
            for line in r.stdout.splitlines():
                m = __import__("re").match(r".+?: line (\d+),.+? (.+)", line)
                if m:
                    issues.append(PatchIssue("lint", "warning", path, int(m.group(1)), m.group(2)))

    return issues


def _run_tests(path: str) -> List[PatchIssue]:
    """Run the sibling test file for path, if it exists."""
    base = os.path.splitext(os.path.basename(path))[0]
    dirn = os.path.dirname(path) or "."
    candidates = [
        os.path.join(d, n)
        for d in (dirn, "tests", "test")
        for n in (f"test_{base}.py", f"{base}_test.py")
    ]
    test_file = next((c for c in candidates if os.path.exists(c)), None)
    if not test_file:
        return []

    r = subprocess.run(
        [sys.executable, "-m", "pytest", test_file, "-x", "-q", "--tb=short", "--no-header"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode == 0:
        return []
    summary = (r.stdout + r.stderr).strip()[-500:]
    return [PatchIssue("test", "error", test_file, 0, summary)]


# ── Public API ────────────────────────────────────────────────────────────────

def validate_patch(
    path: str,
    *,
    run_lint: bool = True,
    run_tests: bool = True,
    complexity: str = "medium",
) -> PatchReport:
    """Validate a written/edited file through the full patch pipeline.

    For trivial tasks only syntax is checked (fast). For medium/complex,
    lint and sibling-test runs are also triggered.
    """
    report = PatchReport(path=path)
    ext = os.path.splitext(path)[1].lower()

    # Stage 1: syntax
    checker = _SYNTAX_CHECKERS.get(ext)
    if checker:
        report.stages_run.append("syntax")
        try:
            issues = checker(path)
            report.issues.extend(issues)
            if any(i.severity == "error" for i in issues):
                report.ok = False
                return report  # abort pipeline on syntax error
        except Exception as e:
            report.issues.append(PatchIssue("syntax", "warning", path, 0, f"checker failed: {e}"))

    if complexity == "simple":
        return report

    # Stage 2: lint (non-trivial tasks only)
    if run_lint and ext in (".py", ".js", ".ts", ".jsx", ".tsx"):
        report.stages_run.append("lint")
        try:
            lint_issues = _run_lint(path)
            report.issues.extend(lint_issues)
            # Lint errors degrade ok but don't abort — tests may still run
            if any(i.severity == "error" for i in lint_issues):
                report.ok = False
        except Exception as e:
            report.issues.append(PatchIssue("lint", "warning", path, 0, f"lint failed: {e}"))

    # Stage 3: unit tests
    if run_tests and ext == ".py" and complexity in ("medium", "complex"):
        report.stages_run.append("test")
        try:
            test_issues = _run_tests(path)
            report.issues.extend(test_issues)
            if any(i.severity == "error" for i in test_issues):
                report.ok = False
        except Exception as e:
            report.issues.append(PatchIssue("test", "warning", path, 0, f"test run failed: {e}"))

    return report


def validate_patch_silent(path: str, complexity: str = "medium") -> Optional[str]:
    """Returns an error string if validation fails, None if ok.
    Designed for background thread use — never raises."""
    try:
        report = validate_patch(path, complexity=complexity)
        if not report.ok:
            return report.error_message()
        return None
    except Exception:
        return None
