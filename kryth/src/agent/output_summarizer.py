"""Tool output summarizer — smart compression of large tool outputs.

SMALL  output (≤30 lines / ≤1,500 chars): pass raw.
MEDIUM output (≤150 lines / ≤8,000 chars): truncate intelligently (head+tail).
LARGE  output (>150 lines / >8,000 chars): summarize — extract signal, drop noise.

Summaries preserve actionable information:
  - Error messages and stack traces
  - Exit codes and statuses
  - Affected file names
  - Warning counts
  - First/last N relevant lines

Telemetry: returns (summary, raw_chars, summary_chars) so callers can log savings.

Usage:
    from agent.output_summarizer import summarize
    compressed, raw_n, out_n = summarize("run_command", raw_output)
"""
from __future__ import annotations

import re

# Thresholds
_SMALL_LINES  = 30
_SMALL_CHARS  = 1_500
_MEDIUM_LINES = 150
_MEDIUM_CHARS = 8_000
_HEAD_LINES   = 10
_TAIL_LINES   = 10

# Pattern matchers for error/warning extraction
_ERROR_LINE_RE = re.compile(
    r"(error|err|fail|fatal|exception|traceback|assert|syntax\s+error|type\s+error"
    r"|import\s+error|module\s+not\s+found|could\s+not|unable\s+to|permission\s+denied"
    r"|connection\s+refused|timeout|not\s+found|no\s+such|FAILED|ERROR|WARN|WARNING"
    r"|\bE\s+[A-Z]|\bF\s+test_)",
    re.I,
)
_WARNING_LINE_RE = re.compile(r"\b(warn|deprecat|caution|note:)\b", re.I)
_SUCCESS_LINE_RE = re.compile(
    r"(successfully|success|done|completed|passed|ok\b|\bbuilt\b|installed|created"
    r"|\d+\s+passed|\d+\s+tests?\s+pass)", re.I,
)
_FILE_PATH_RE  = re.compile(r"[\w./\\-]+\.(py|js|ts|tsx|go|rs|java|rb|c|h|cpp)\b", re.I)
_EXIT_CODE_RE  = re.compile(r"(exit\s+code|return\s+code|exited\s+with)\s*[:\s]\s*(\d+)", re.I)


# ── Tool-specific strategies ──────────────────────────────────────────────────

def _summarize_npm(output: str) -> str:
    lines = output.splitlines()
    errors: list[str] = []
    warnings: list[str] = []
    affected_pkgs: list[str] = []
    _pkg_re = re.compile(r"npm\s+(ERR!|WARN)\s+(.*)", re.I)

    for line in lines:
        m = _pkg_re.search(line)
        if m:
            kind, msg = m.group(1).upper(), m.group(2).strip()
            if kind == "ERR!" and msg not in errors:
                errors.append(msg[:120])
            elif kind == "WARN" and msg not in warnings:
                warnings.append(msg[:100])
        # Capture added/removed package lines
        if re.search(r"added \d+|removed \d+|changed \d+|audited \d+", line, re.I):
            affected_pkgs.append(line.strip()[:100])

    parts = []
    if errors:
        parts.append("Errors:\n" + "\n".join(f"  {e}" for e in errors[:10]))
    if warnings:
        parts.append(f"Warnings ({len(warnings)} total):\n" + "\n".join(f"  {w}" for w in warnings[:5]))
    if affected_pkgs:
        parts.append("Packages:\n" + "\n".join(f"  {p}" for p in affected_pkgs[:5]))
    if not parts:
        return _generic_head_tail(output)
    return "[npm output summary]\n" + "\n".join(parts)


def _summarize_pip(output: str) -> str:
    lines = output.splitlines()
    errors = [l.strip() for l in lines if re.search(r"\b(error|ERROR|fail|could not)\b", l)]
    installed = [l.strip() for l in lines if re.search(r"\b(Successfully installed|Requirement already)\b", l)]
    parts = []
    if errors:
        parts.append("Errors:\n" + "\n".join(f"  {e[:120]}" for e in errors[:8]))
    if installed:
        parts.append("Installed:\n" + "\n".join(f"  {i[:120]}" for i in installed[:5]))
    if not parts:
        return _generic_head_tail(output)
    return "[pip output summary]\n" + "\n".join(parts)


def _summarize_pytest(output: str) -> str:
    lines = output.splitlines()
    failures: list[str] = []
    summary_line = ""
    in_failure = False
    failure_buf: list[str] = []

    for line in lines:
        # FAILED test_xxx.py::test_foo - AssertionError
        if re.match(r"^FAILED\s", line) or re.match(r"^ERROR\s", line):
            failures.append(line.strip()[:140])
        # Capture short failure sections (FAIL: / E  AssertionError)
        if re.match(r"^={3,}\s+FAILURES\s+=", line, re.I):
            in_failure = True
        if in_failure:
            failure_buf.append(line)
            if re.match(r"^={3,}$", line) and len(failure_buf) > 1:
                in_failure = False
        # Summary line: "3 failed, 12 passed in 0.45s"
        if re.match(r"^={3,}\s+\d+\s+(failed|passed|error)", line, re.I):
            summary_line = line.strip()

    parts = []
    if summary_line:
        parts.append(f"Result: {summary_line}")
    if failures:
        parts.append("Failed:\n" + "\n".join(f"  {f}" for f in failures[:10]))
    if failure_buf:
        # Include first failure detail (≤20 lines)
        detail = "\n".join(failure_buf[:20])
        parts.append(f"Detail:\n{detail}")
    if not parts:
        return _generic_head_tail(output)
    return "[pytest summary]\n" + "\n".join(parts)


def _summarize_jest(output: str) -> str:
    lines = output.splitlines()
    fails = [l.strip() for l in lines if re.search(r"(✕|✗|×|FAIL|FAILED)\s", l)]
    summary = next((l.strip() for l in reversed(lines) if re.search(r"Tests:\s+\d+", l)), "")
    parts = []
    if summary:
        parts.append(f"Result: {summary}")
    if fails:
        parts.append("Failed:\n" + "\n".join(f"  {f[:140]}" for f in fails[:10]))
    if not parts:
        return _generic_head_tail(output)
    return "[jest summary]\n" + "\n".join(parts)


def _summarize_git_log(output: str) -> str:
    lines = output.splitlines()
    return "\n".join(lines[:20]) + (f"\n...({len(lines)-20} more lines)" if len(lines) > 20 else "")


def _summarize_git_diff(output: str) -> str:
    lines = output.splitlines()
    if len(lines) <= 40:
        return output
    # Keep file-level summary lines (--- +++ @@)
    headers = [l for l in lines if l.startswith(("---", "+++", "diff --", "@@", "index "))]
    head = "\n".join(lines[:15])
    return head + f"\n...({len(lines)-15} lines; {len(headers)} file sections)"


def _summarize_tree(output: str) -> str:
    lines = output.splitlines()
    if len(lines) <= 40:
        return output
    return "\n".join(lines[:40]) + f"\n...({len(lines)-40} more entries)"


def _summarize_error_output(output: str) -> str:
    """For run_command / shell_exec when output is large and has errors."""
    lines = output.splitlines()
    error_lines = [l for l in lines if _ERROR_LINE_RE.search(l)]
    warning_lines = [l for l in lines if _WARNING_LINE_RE.search(l) and not _ERROR_LINE_RE.search(l)]
    files = list({m.group(0) for l in lines for m in [_FILE_PATH_RE.search(l)] if m})

    # Extract exit code if present
    exit_code = ""
    for line in reversed(lines[-5:]):
        m = _EXIT_CODE_RE.search(line)
        if m:
            exit_code = f"Exit code: {m.group(2)}"
            break

    parts = []
    if exit_code:
        parts.append(exit_code)
    if error_lines:
        parts.append(f"Errors ({len(error_lines)}):\n" + "\n".join(f"  {e[:140]}" for e in error_lines[:15]))
    if warning_lines:
        parts.append(f"Warnings ({len(warning_lines)}): {warning_lines[0][:100]}" +
                     (f" ... +{len(warning_lines)-1} more" if len(warning_lines) > 1 else ""))
    if files:
        parts.append("Affected files: " + ", ".join(files[:8]))
    if not parts:
        return _generic_head_tail(output)
    return "[output summary]\n" + "\n".join(parts)


def _generic_head_tail(output: str) -> str:
    lines = output.splitlines()
    if len(lines) <= _HEAD_LINES + _TAIL_LINES:
        return output
    head = lines[:_HEAD_LINES]
    tail = lines[-_TAIL_LINES:]
    dropped = len(lines) - _HEAD_LINES - _TAIL_LINES
    return "\n".join(head) + f"\n...({dropped} lines omitted)...\n" + "\n".join(tail)


# ── Routing table ─────────────────────────────────────────────────────────────

def _detect_output_type(tool_name: str, output: str) -> str:
    """Heuristic output type detection."""
    if tool_name in ("run_install",) or re.search(r"npm\s+(install|i|ci)\b|yarn\b", output[:200], re.I):
        if "npm " in output or "yarn " in output:
            return "npm"
        return "pip"
    if tool_name == "run_tests" or re.search(r"pytest|py\.test|===.*passed|===.*failed", output[:500], re.I):
        return "pytest"
    if re.search(r"jest|vitest|PASS\s+\w|FAIL\s+\w|Tests:\s+\d+", output[:500], re.I):
        return "jest"
    if tool_name == "git_op":
        if re.search(r"^commit\s+[0-9a-f]{7}", output[:200], re.M):
            return "git_log"
        if re.search(r"^diff --git\b", output[:200], re.M):
            return "git_diff"
    if re.search(r"pip\s+install|Collecting\s+\w|Successfully installed", output[:200], re.I):
        return "pip"
    if re.search(r"^(\s*[├└│]|\s{0,4}[A-Za-z_.].*\/)", output[:400], re.M):
        return "tree"
    return "generic"


_READ_EXEMPT_TOOLS = frozenset({
    "read_file",
})


def summarize(
    tool_name: str,
    raw_output: str,
) -> tuple[str, int, int]:
    """Compress a tool output if it's too large.

    Returns (compressed_output, raw_chars, compressed_chars).
    When raw is small, compressed_output == raw_output and chars are equal.

    read_file is exempt — file content is never summarized or truncated here.
    """
    raw_chars = len(raw_output)
    lines = raw_output.count("\n") + 1

    # read_file is exempt: file content passes through verbatim
    if tool_name in _READ_EXEMPT_TOOLS:
        return raw_output, raw_chars, raw_chars

    # Small: pass through unchanged
    if lines <= _SMALL_LINES and raw_chars <= _SMALL_CHARS:
        return raw_output, raw_chars, raw_chars

    # Detect output type for strategy routing
    output_type = _detect_output_type(tool_name, raw_output)

    # Medium: generic truncation (head+tail)
    if lines <= _MEDIUM_LINES and raw_chars <= _MEDIUM_CHARS:
        result = _generic_head_tail(raw_output)
        return result, raw_chars, len(result)

    # Large: strategy-specific summarization
    if output_type == "npm":
        result = _summarize_npm(raw_output)
    elif output_type == "pip":
        result = _summarize_pip(raw_output)
    elif output_type == "pytest":
        result = _summarize_pytest(raw_output)
    elif output_type == "jest":
        result = _summarize_jest(raw_output)
    elif output_type == "git_log":
        result = _summarize_git_log(raw_output)
    elif output_type == "git_diff":
        result = _summarize_git_diff(raw_output)
    elif output_type == "tree":
        result = _summarize_tree(raw_output)
    else:
        # Generic: check for errors first, then head+tail
        has_errors = bool(_ERROR_LINE_RE.search(raw_output))
        if has_errors:
            result = _summarize_error_output(raw_output)
        else:
            result = _generic_head_tail(raw_output)

    return result, raw_chars, len(result)
