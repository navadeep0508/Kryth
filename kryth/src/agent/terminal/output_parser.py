"""Phase 6 — Intelligent Output Parser.

Converts raw command output into structured objects.  Never sends raw
5000-line logs to the LLM — only structured ParsedOutput.

Detects:
  - Python tracebacks (single and chained)
  - Node.js / TypeScript stack traces
  - Rust / Cargo compile errors
  - npm / yarn / pnpm install errors
  - pytest failure summaries
  - TypeScript tsc errors
  - Docker build / run errors
  - Git errors and conflicts
  - Generic non-zero exit errors
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ErrorKind(str, Enum):
    PYTHON_TRACEBACK = "python_traceback"
    NODE_STACK = "node_stack"
    RUST_COMPILE = "rust_compile"
    CARGO_ERROR = "cargo_error"
    NPM_ERROR = "npm_error"
    PYTEST_FAILURE = "pytest_failure"
    TS_ERROR = "ts_error"
    DOCKER_ERROR = "docker_error"
    GIT_ERROR = "git_error"
    GENERIC = "generic"
    SUCCESS = "success"


@dataclass
class ErrorLocation:
    file: str = ""
    line: int = 0
    column: int = 0
    function: str = ""


@dataclass
class ParsedError:
    kind: ErrorKind = ErrorKind.GENERIC
    message: str = ""
    error_type: str = ""
    location: Optional[ErrorLocation] = None
    suggested_fix: str = ""
    raw_snippet: str = ""    # 5-line context around the error


@dataclass
class ParsedOutput:
    success: bool = False
    exit_code: int = 0
    errors: list[ParsedError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""
    raw_length: int = 0

    def primary_error(self) -> Optional[ParsedError]:
        return self.errors[0] if self.errors else None

    def to_llm_context(self) -> str:
        """Compact representation safe to send to the LLM."""
        if self.success:
            return f"SUCCESS (rc=0) — {self.summary}" if self.summary else "SUCCESS (rc=0)"
        parts = [f"FAILED (rc={self.exit_code})"]
        for err in self.errors[:3]:
            parts.append(f"[{err.kind.value}] {err.error_type}: {err.message}")
            if err.location and err.location.file:
                loc = err.location
                parts.append(f"  at {loc.file}:{loc.line}")
            if err.suggested_fix:
                parts.append(f"  fix: {err.suggested_fix}")
            if err.raw_snippet:
                parts.append(f"  context:\n{err.raw_snippet}")
        if self.warnings:
            parts.append(f"warnings ({len(self.warnings)}): {self.warnings[0]}")
        return "\n".join(parts)


# ---- Regex patterns ------------------------------------------------

_PY_TRACEBACK_START = re.compile(r"^Traceback \(most recent call last\):", re.M)
_PY_TRACEBACK_FILE = re.compile(r'File "([^"]+)", line (\d+)(?:, in (.+))?')
_PY_ERROR_LINE = re.compile(r'^(\w[\w.]*(?:Error|Exception|Warning|KeyboardInterrupt|SystemExit)[^\n]*)', re.M)

_NODE_STACK_START = re.compile(r'^(Error|TypeError|RangeError|ReferenceError|SyntaxError): ', re.M)
_NODE_STACK_FRAME = re.compile(r'^\s+at .+ \((.+):(\d+):\d+\)', re.M)

_TS_ERROR = re.compile(r'^([^\s]+\.tsx?)\((\d+),(\d+)\): error TS(\d+): (.+)$', re.M)

_RUST_ERROR = re.compile(
    r'^error(?:\[E\d+\])?: (.+)\n(?:.*\n)*?\s+-->\s+([^\s:]+):(\d+):(\d+)',
    re.M,
)
_CARGO_FAIL = re.compile(r'^error: could not compile', re.M)

_NPM_ERR = re.compile(r'^npm (ERR!|WARN) (.+)$', re.M)
_YARN_ERR = re.compile(r'^error (.+)$', re.M)

_PYTEST_FAIL = re.compile(
    r'(?:FAILED|ERROR) ([^\s]+\.py)(?:::(.+))? - (.+)',
)
_PYTEST_SUMMARY = re.compile(
    r'=+ short test summary info =+\n((?:FAILED .+\n)*)',
    re.M,
)
_PYTEST_COUNTS = re.compile(
    r'(\d+) (passed|failed|error|warning)s?',
    re.I,
)

_DOCKER_ERR = re.compile(r'^(?:Error response from daemon|error during connect|failed to): (.+)$', re.M)
_DOCKER_BUILD_ERR = re.compile(r'Step \d+/\d+ : .+\n +-{2,}> .+\n(?:.*\n)*?(?:.*Error.*\n)', re.M)

_GIT_ERR_PATTERNS = [
    re.compile(r"^error: (.+)$", re.M),
    re.compile(r"^fatal: (.+)$", re.M),
    re.compile(r"^CONFLICT \((.+)\): Merge conflict in (.+)$", re.M),
]


# ---- Detectors -----------------------------------------------------

def _detect_python_traceback(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    matches = list(_PY_TRACEBACK_START.finditer(text))
    for m in matches:
        block_start = m.start()
        # Find the end of this traceback block (blank line or next traceback)
        next_start = (
            matches[matches.index(m) + 1].start()
            if matches.index(m) + 1 < len(matches)
            else len(text)
        )
        block = text[block_start:next_start]

        # Last file frame
        locations = _PY_TRACEBACK_FILE.findall(block)
        loc = None
        if locations:
            f, ln, fn = locations[-1]
            loc = ErrorLocation(file=f, line=int(ln), function=fn)

        # Error type + message (last non-empty line)
        lines = [l for l in block.splitlines() if l.strip()]
        last_line = lines[-1] if lines else ""
        em = _PY_ERROR_LINE.search(last_line)
        error_type = ""
        message = last_line
        if ":" in last_line:
            parts = last_line.split(":", 1)
            error_type = parts[0].strip()
            message = parts[1].strip()

        snippet = "\n".join(lines[-5:]) if len(lines) >= 5 else "\n".join(lines)

        errors.append(ParsedError(
            kind=ErrorKind.PYTHON_TRACEBACK,
            message=message,
            error_type=error_type,
            location=loc,
            suggested_fix=_python_fix_hint(error_type, message),
            raw_snippet=snippet,
        ))
    return errors


def _python_fix_hint(error_type: str, message: str) -> str:
    msg_lower = message.lower()
    if "modulenotfounderror" in error_type or "no module named" in msg_lower:
        mod = re.search(r"No module named '([^']+)'", message)
        pkg = mod.group(1).replace(".", "-") if mod else "package"
        return f"pip install {pkg}"
    if "filenotfounderror" in error_type:
        return "check the file path exists"
    if "permissionerror" in error_type:
        return "check file/directory permissions"
    if "syntaxerror" in error_type:
        return "fix the syntax on the indicated line"
    if "attributeerror" in error_type:
        return "check object type or import"
    if "keyerror" in error_type:
        return "verify dict key exists before accessing"
    if "typeerror" in error_type:
        return "check argument types"
    if "valueerror" in error_type:
        return "validate the value before conversion"
    return ""


def _detect_node_stack(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    for m in _NODE_STACK_START.finditer(text):
        block_end = text.find("\n\n", m.start())
        block = text[m.start(): block_end if block_end != -1 else m.start() + 800]
        lines = block.splitlines()
        header = lines[0] if lines else ""
        error_type, _, message = header.partition(": ")

        frames = _NODE_STACK_FRAME.findall(block)
        loc = None
        if frames:
            file, line = frames[0][0], int(frames[0][1])
            if not file.startswith("node:"):
                loc = ErrorLocation(file=file, line=line)

        errors.append(ParsedError(
            kind=ErrorKind.NODE_STACK,
            message=message.strip(),
            error_type=error_type.strip(),
            location=loc,
            raw_snippet="\n".join(lines[:8]),
        ))
    return errors


def _detect_ts_errors(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    for m in _TS_ERROR.finditer(text):
        errors.append(ParsedError(
            kind=ErrorKind.TS_ERROR,
            message=m.group(5),
            error_type=f"TS{m.group(4)}",
            location=ErrorLocation(file=m.group(1), line=int(m.group(2)), column=int(m.group(3))),
            raw_snippet=m.group(0),
        ))
    return errors


def _detect_rust_errors(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    for m in _RUST_ERROR.finditer(text):
        errors.append(ParsedError(
            kind=ErrorKind.RUST_COMPILE,
            message=m.group(1),
            error_type="compile error",
            location=ErrorLocation(file=m.group(2), line=int(m.group(3))),
            raw_snippet=m.group(0)[:300],
        ))
    return errors


def _detect_npm_errors(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    for m in _NPM_ERR.finditer(text):
        if m.group(1) == "ERR!":
            errors.append(ParsedError(
                kind=ErrorKind.NPM_ERROR,
                message=m.group(2),
                error_type="npm error",
                raw_snippet=m.group(0),
            ))
    return errors


def _detect_pytest_failures(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    # Use summary block first
    m = _PYTEST_SUMMARY.search(text)
    block = m.group(1) if m else text
    for m2 in _PYTEST_FAIL.finditer(block):
        errors.append(ParsedError(
            kind=ErrorKind.PYTEST_FAILURE,
            message=m2.group(3),
            error_type="test failure",
            location=ErrorLocation(
                file=m2.group(1),
                function=m2.group(2) or "",
            ),
            raw_snippet=m2.group(0),
        ))
    return errors


def _detect_docker_errors(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    for m in _DOCKER_ERR.finditer(text):
        errors.append(ParsedError(
            kind=ErrorKind.DOCKER_ERROR,
            message=m.group(1),
            error_type="docker error",
            raw_snippet=m.group(0),
        ))
    return errors


def _detect_git_errors(text: str) -> list[ParsedError]:
    errors: list[ParsedError] = []
    for pattern in _GIT_ERR_PATTERNS:
        for m in pattern.finditer(text):
            errors.append(ParsedError(
                kind=ErrorKind.GIT_ERROR,
                message=m.group(1) if pattern.groups else m.group(0),
                error_type="git error",
                raw_snippet=m.group(0),
            ))
    return errors


# ---- Main parser ---------------------------------------------------

class OutputParser:
    """Parse raw terminal output into structured ParsedOutput."""

    _DETECTORS = [
        _detect_python_traceback,
        _detect_node_stack,
        _detect_ts_errors,
        _detect_rust_errors,
        _detect_npm_errors,
        _detect_pytest_failures,
        _detect_docker_errors,
        _detect_git_errors,
    ]

    def parse(self, raw: str, exit_code: int = 0) -> ParsedOutput:
        result = ParsedOutput(
            success=(exit_code == 0),
            exit_code=exit_code,
            raw_length=len(raw),
        )

        if exit_code == 0 and not _looks_like_error(raw):
            result.summary = self._extract_success_summary(raw)
            return result

        for detector in self._detectors:
            try:
                found = detector(raw)
                result.errors.extend(found)
            except Exception:
                pass

        # Warnings
        result.warnings = self._extract_warnings(raw)

        # Fallback: generic error if nothing matched
        if not result.errors and exit_code != 0:
            last_lines = "\n".join(raw.splitlines()[-10:])
            result.errors.append(ParsedError(
                kind=ErrorKind.GENERIC,
                message=f"command failed with exit code {exit_code}",
                raw_snippet=last_lines,
            ))

        result.summary = self._build_summary(result)
        return result

    _detectors = [
        _detect_python_traceback,
        _detect_node_stack,
        _detect_ts_errors,
        _detect_rust_errors,
        _detect_npm_errors,
        _detect_pytest_failures,
        _detect_docker_errors,
        _detect_git_errors,
    ]

    def _extract_success_summary(self, raw: str) -> str:
        # pytest counts
        counts = _PYTEST_COUNTS.findall(raw)
        if counts:
            parts = [f"{c} {k}" for c, k in counts]
            return "pytest: " + ", ".join(parts)
        # Last meaningful line
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        return lines[-1][:120] if lines else ""

    def _extract_warnings(self, raw: str) -> list[str]:
        warn_re = re.compile(r'(?:warning|WARN)[:\s]+(.{20,120})', re.I)
        return [m.group(1) for m in warn_re.finditer(raw)][:10]

    def _build_summary(self, result: ParsedOutput) -> str:
        if not result.errors:
            return f"exit code {result.exit_code}"
        e = result.errors[0]
        return f"{e.kind.value}: {e.error_type or e.message}"[:120]


def _looks_like_error(text: str) -> bool:
    lower = text.lower()
    return any(
        marker in lower
        for marker in ("traceback", "error:", "syntaxerror", "exception", "failed", "fatal:")
    )


# Module-level singleton
output_parser = OutputParser()
