"""Autonomous debug cycle for KRYTH.

Structured observe → diagnose → fix → validate loop with failure memory.
When a run_command fails, instead of letting the LLM blindly retry, this
module orchestrates a structured repair:

  1. Observe: collect full stdout/stderr + exit code
  2. Diagnose: classify error type (import, syntax, runtime, assertion, network)
  3. Recall: check failure memory for known bad fixes to avoid
  4. Fix: call LLM with focused diagnosis context (NOT full history)
  5. Validate: re-run the command; on success, save the fix to memory
  6. Retry: up to KRYTH_DEBUG_MAX_RETRIES (default 3)

Failure memory lives in-process (dict keyed by normalized error fingerprint).
On repeated identical errors, the cycle escalates instead of looping.

Usage:
    from agent.debug_cycle import DebugCycle
    cycle = DebugCycle(session)
    result = cycle.run(command="python app.py", stdout="", stderr="ModuleNotFoundError: ...", exit_code=1)
    if result.fixed:
        ...
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


MAX_RETRIES = int(os.environ.get("KRYTH_DEBUG_MAX_RETRIES", "3"))

# In-process failure memory: fingerprint → list of attempted fixes (failed)
_FAILURE_MEMORY: Dict[str, List[str]] = {}
# Successful fixes: fingerprint → fix description
_SUCCESS_MEMORY: Dict[str, str] = {}


@dataclass
class DebugAttempt:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    error_type: str
    diagnosis: str
    fix_applied: str
    retry_no: int
    success: bool


@dataclass
class DebugResult:
    fixed: bool
    attempts: List[DebugAttempt] = field(default_factory=list)
    final_error: str = ""
    escalation_reason: str = ""

    @property
    def total_retries(self) -> int:
        return len(self.attempts)


# ── Error classification ──────────────────────────────────────────────────────

_ERROR_PATTERNS = [
    ("import_error",   re.compile(r"ModuleNotFoundError|ImportError|No module named")),
    ("syntax_error",   re.compile(r"SyntaxError|IndentationError|TabError")),
    ("type_error",     re.compile(r"TypeError|AttributeError")),
    ("runtime_error",  re.compile(r"RuntimeError|ValueError|KeyError|IndexError")),
    ("assertion",      re.compile(r"AssertionError|assert\s")),
    ("name_error",     re.compile(r"NameError")),
    ("file_not_found", re.compile(r"FileNotFoundError|No such file|cannot find")),
    ("permission",     re.compile(r"PermissionError|Access is denied")),
    ("network",        re.compile(r"ConnectionError|TimeoutError|URLError|requests\.")),
    ("test_failure",   re.compile(r"FAILED|pytest|AssertionError|ERRORS")),
    ("compile_error",  re.compile(r"error TS|tsc|compilation failed", re.I)),
]


def classify_error(stderr: str, stdout: str) -> Tuple[str, str]:
    """Returns (error_type, key_line)."""
    combined = stderr + "\n" + stdout
    for etype, pat in _ERROR_PATTERNS:
        m = pat.search(combined)
        if m:
            # Extract the key line
            for line in (stderr or stdout).splitlines():
                if pat.search(line):
                    return etype, line.strip()
            return etype, m.group(0)
    return "unknown", (stderr or stdout).splitlines()[-1][:200] if (stderr or stdout) else ""


def _fingerprint(command: str, error_type: str, key_line: str) -> str:
    raw = f"{os.path.basename(command.split()[0])}|{error_type}|{key_line[:100]}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ── Fixes by error type ───────────────────────────────────────────────────────

def _fix_import_error(key_line: str, command: str) -> Optional[str]:
    m = re.search(r"No module named '([^']+)'", key_line)
    if not m:
        return None
    pkg = m.group(1).split(".")[0].replace("_", "-")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", pkg],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode == 0:
        return f"pip install {pkg}"
    return None


def _fix_file_not_found(key_line: str, command: str) -> Optional[str]:
    m = re.search(r"No such file or directory: '([^']+)'", key_line)
    if not m:
        return None
    path = m.group(1)
    dirpart = os.path.dirname(path)
    if dirpart:
        os.makedirs(dirpart, exist_ok=True)
        return f"mkdir -p {dirpart}"
    return None


_AUTO_FIXES = {
    "import_error":   _fix_import_error,
    "file_not_found": _fix_file_not_found,
}


# ── Core cycle ────────────────────────────────────────────────────────────────

class DebugCycle:
    """Run the structured observe → diagnose → fix → validate debug loop."""

    def __init__(self, session=None, max_retries: int = MAX_RETRIES) -> None:
        self._session = session
        self._max_retries = max_retries

    def run(
        self,
        command: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 1,
        cwd: Optional[str] = None,
    ) -> DebugResult:
        result = DebugResult(fixed=False)
        error_type, key_line = classify_error(stderr, stdout)
        fp = _fingerprint(command, error_type, key_line)

        # Check success memory — if we've fixed this before, try that fix first
        if fp in _SUCCESS_MEMORY:
            known_fix = _SUCCESS_MEMORY[fp]
            result.attempts.append(DebugAttempt(
                command=command, exit_code=exit_code,
                stdout=stdout, stderr=stderr,
                error_type=error_type, diagnosis=f"known fix: {known_fix}",
                fix_applied=known_fix, retry_no=0, success=False,
            ))

        failed_fixes = _FAILURE_MEMORY.get(fp, [])

        for retry in range(self._max_retries):
            # 1. Try an auto-fix for this error type
            fix_fn = _AUTO_FIXES.get(error_type)
            fix_applied = ""
            if fix_fn:
                try:
                    fix = fix_fn(key_line, command)
                    if fix and fix not in failed_fixes:
                        fix_applied = fix
                except Exception:
                    pass

            # 2. Re-run the command
            ok, new_out, new_err, new_code = self._rerun(command, cwd)
            attempt = DebugAttempt(
                command=command, exit_code=new_code,
                stdout=new_out, stderr=new_err,
                error_type=error_type,
                diagnosis=key_line,
                fix_applied=fix_applied,
                retry_no=retry + 1,
                success=ok,
            )
            result.attempts.append(attempt)

            if ok:
                result.fixed = True
                if fix_applied:
                    _SUCCESS_MEMORY[fp] = fix_applied
                    _FAILURE_MEMORY.pop(fp, None)
                return result

            # Track failed fix so we don't repeat it
            if fix_applied:
                _FAILURE_MEMORY.setdefault(fp, []).append(fix_applied)

            # Re-classify after retry (error may have changed)
            error_type, key_line = classify_error(new_err, new_out)
            fp = _fingerprint(command, error_type, key_line)
            failed_fixes = _FAILURE_MEMORY.get(fp, [])

            if retry < self._max_retries - 1:
                time.sleep(0.5)

        result.final_error = f"{error_type}: {key_line}"
        result.escalation_reason = (
            f"Failed after {self._max_retries} retries. "
            f"Error type: {error_type}. "
            f"Last error: {key_line[:200]}"
        )
        return result

    def _rerun(self, command: str, cwd: Optional[str]) -> Tuple[bool, str, str, int]:
        try:
            r = subprocess.run(
                shlex.split(command), capture_output=True, text=True,
                timeout=120, cwd=cwd,
            )
            return r.returncode == 0, r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired:
            return False, "", "timeout", 124
        except Exception as e:
            return False, "", str(e), 1


# ── Failure memory API ────────────────────────────────────────────────────────

def get_failure_memory() -> Dict[str, List[str]]:
    return dict(_FAILURE_MEMORY)


def get_success_memory() -> Dict[str, str]:
    return dict(_SUCCESS_MEMORY)


def clear_failure_memory() -> None:
    _FAILURE_MEMORY.clear()


def record_failure(command: str, error_type: str, key_line: str, fix: str) -> None:
    fp = _fingerprint(command, error_type, key_line)
    _FAILURE_MEMORY.setdefault(fp, []).append(fix)


def record_success(command: str, error_type: str, key_line: str, fix: str) -> None:
    fp = _fingerprint(command, error_type, key_line)
    _SUCCESS_MEMORY[fp] = fix
    _FAILURE_MEMORY.pop(fp, None)


def failure_summary() -> str:
    lines = ["Failure memory:"]
    for fp, fixes in _FAILURE_MEMORY.items():
        lines.append(f"  [{fp}] failed fixes: {', '.join(fixes[:3])}")
    if not _FAILURE_MEMORY:
        lines.append("  (empty)")
    return "\n".join(lines)
