"""Project-aware test + install wrappers.

Given a working directory, detect the project type from marker files
(pyproject.toml, package.json, Cargo.toml, go.mod) and run the canonical
test / install command for that stack. The agent gets:

  * a one-line headline (PASS / FAIL / counts when available)
  * the tail of stdout/stderr
  * the underlying command + exit code so it can re-run by hand

Why not just teach the agent the right command? Because the right
command differs by repo (pytest vs unittest, npm vs pnpm vs yarn) and
shows up in stack-specific lockfiles the agent would otherwise have to
detect on every turn. Centralising it here means one tool call instead
of three.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from agent.tools._results import err


PROJECT_TEST_TIMEOUT = 300        # seconds — pytest / cargo test runs
PROJECT_INSTALL_TIMEOUT = 600     # seconds — installs can be slow
TAIL_LINES = 60


def _detect_stack(cwd: str = ".") -> str:
    """Return a stack label or 'unknown' based on marker files."""
    base = Path(cwd)
    if (base / "pyproject.toml").exists() or (base / "setup.py").exists() \
            or (base / "requirements.txt").exists():
        return "python"
    if (base / "package.json").exists():
        return "node"
    if (base / "Cargo.toml").exists():
        return "rust"
    if (base / "go.mod").exists():
        return "go"
    return "unknown"


def _node_package_manager(cwd: str = ".") -> str:
    """pnpm > yarn > npm, based on lockfile presence."""
    base = Path(cwd)
    if (base / "pnpm-lock.yaml").exists() and shutil.which("pnpm"):
        return "pnpm"
    if (base / "yarn.lock").exists() and shutil.which("yarn"):
        return "yarn"
    return "npm"


def _run(args: list[str], cwd: str, timeout: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return 127, "", f"executable not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(args)} timed out after {timeout}s"
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {e}"
    return proc.returncode, proc.stdout, proc.stderr


def _tail(text: str, n: int = TAIL_LINES) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= n:
        return "\n".join(lines)
    return "\n".join(lines[-n:])


# Common runner regexes for headline extraction. Best-effort — no
# regex is a substitute for parsing the runner's structured output,
# but this saves the agent a follow-up scan in 80% of cases.
_PYTEST_SUMMARY = re.compile(
    r"(?P<passed>\d+) passed|(?P<failed>\d+) failed|(?P<errors>\d+) error",
    re.IGNORECASE,
)
_JEST_SUMMARY = re.compile(
    r"Tests?:\s+(?:(\d+) failed,\s+)?(?:(\d+) passed)",
)
_GO_SUMMARY = re.compile(r"\b(?:PASS|FAIL|ok|--- FAIL)\b")
_CARGO_SUMMARY = re.compile(
    r"test result:\s+(?P<verdict>\w+)\.\s+(?P<passed>\d+) passed;\s+(?P<failed>\d+) failed",
)


def _headline_python(rc: int, out: str, err_text: str) -> str:
    text = out + "\n" + err_text
    passed = failed = errors = 0
    for m in _PYTEST_SUMMARY.finditer(text):
        if m.group("passed"): passed = int(m.group("passed"))
        if m.group("failed"): failed = int(m.group("failed"))
        if m.group("errors"): errors = int(m.group("errors"))
    if passed or failed or errors:
        return f"pytest: {passed} passed, {failed} failed, {errors} errors (rc={rc})"
    return f"python tests rc={rc}"


def _headline_node(rc: int, out: str, err_text: str) -> str:
    text = out + "\n" + err_text
    m = _JEST_SUMMARY.search(text)
    if m:
        failed = int(m.group(1) or 0)
        passed = int(m.group(2) or 0)
        return f"jest/vitest: {passed} passed, {failed} failed (rc={rc})"
    return f"node tests rc={rc}"


def _headline_rust(rc: int, out: str, err_text: str) -> str:
    m = _CARGO_SUMMARY.search(out + "\n" + err_text)
    if m:
        return (
            f"cargo test: {m.group('verdict')}, "
            f"{m.group('passed')} passed, {m.group('failed')} failed (rc={rc})"
        )
    return f"cargo test rc={rc}"


def _headline_go(rc: int, out: str, err_text: str) -> str:
    text = out + "\n" + err_text
    fail = "FAIL" in text and rc != 0
    return f"go test {'FAIL' if fail else 'OK'} (rc={rc})"


def run_tests(cwd: str = ".", changed_paths: list | None = None):
    """Detect the project's test runner and execute it. Returns a
    headline + tail of output. Output is always bounded so a verbose
    runner doesn't blow up the agent's context.

    If changed_paths is provided, checks the test cache first and skips
    tests whose inputs haven't changed since the last successful run.
    """
    stack = _detect_stack(cwd)

    # Test cache check — skip when inputs unchanged
    if changed_paths is not None:
        # Test cache removed with orchestration subsystem
        pass
        _record_pass = True
    else:
        _record_pass = False

    if stack == "python":
        if shutil.which("pytest"):
            rc, out, errtext = _run(["pytest", "-q", "--tb=short"], cwd, PROJECT_TEST_TIMEOUT)
            headline = _headline_python(rc, out, errtext)
        else:
            rc, out, errtext = _run(
                ["python", "-m", "unittest", "discover", "-v"],
                cwd, PROJECT_TEST_TIMEOUT,
            )
            headline = f"unittest rc={rc}"
    elif stack == "node":
        pm = _node_package_manager(cwd)
        rc, out, errtext = _run([pm, "test", "--silent"], cwd, PROJECT_TEST_TIMEOUT)
        headline = _headline_node(rc, out, errtext)
    elif stack == "rust":
        rc, out, errtext = _run(["cargo", "test", "--quiet"], cwd, PROJECT_TEST_TIMEOUT)
        headline = _headline_rust(rc, out, errtext)
    elif stack == "go":
        rc, out, errtext = _run(["go", "test", "./..."], cwd, PROJECT_TEST_TIMEOUT)
        headline = _headline_go(rc, out, errtext)
    else:
        return err(
            "INVALID_STATE",
            f"no recognised project markers in {cwd}",
            "expected one of: pyproject.toml, package.json, Cargo.toml, go.mod",
        )

    body = _tail(out) or _tail(errtext) or "(no output)"
    return f"{headline}\n--- output tail ---\n{body}"


def run_install(cwd: str = "."):
    """Detect the project's package manager and run its install command.

    Honors lockfiles (pnpm-lock → pnpm install, yarn.lock → yarn).
    """
    stack = _detect_stack(cwd)

    if stack == "python":
        base = Path(cwd)
        if (base / "pyproject.toml").exists():
            args = ["pip", "install", "-e", "."]
        elif (base / "requirements.txt").exists():
            args = ["pip", "install", "-r", "requirements.txt"]
        else:
            return err("INVALID_STATE", "no requirements.txt or pyproject.toml in cwd")
    elif stack == "node":
        pm = _node_package_manager(cwd)
        args = [pm, "install"]
    elif stack == "rust":
        args = ["cargo", "build"]
    elif stack == "go":
        args = ["go", "mod", "tidy"]
    else:
        return err(
            "INVALID_STATE",
            f"no recognised project markers in {cwd}",
            "expected one of: pyproject.toml, package.json, Cargo.toml, go.mod",
        )

    rc, out, errtext = _run(args, cwd, PROJECT_INSTALL_TIMEOUT)
    headline = f"{' '.join(args)} → rc={rc}"
    body = _tail(out) or _tail(errtext) or "(no output)"
    return f"{headline}\n--- output tail ---\n{body}"


__all__ = ["run_tests", "run_install", "_detect_stack"]
