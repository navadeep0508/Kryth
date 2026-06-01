"""Language-aware output summarization.

When a shell command produces 800 lines of build output, we should not
dump 800 lines into the user's terminal. We also should not blindly
trim to 80 — useful information lives near the end (final error,
"FAILED" markers, exit summary). This module compresses a raw output
into a render-friendly shape:

    OutputSummary(
        first_lines:  list[str]   # head, capped
        last_lines:   list[str]   # tail, capped
        hidden:       int         # lines elided in between
        headline:     str | None  # detected verdict, e.g. "32 passed, 1 failed"
        errors:       int
        warnings:     int
    )

Detection is heuristic but covers the heavy hitters: pytest, jest/vitest,
mocha, eslint, tsc, webpack/vite/rollup, npm, pip, generic Python
tracebacks, generic compile errors. When detection fails, we still
return a clean head+tail trim — never worse than the naive form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class OutputSummary:
    first_lines: list[str] = field(default_factory=list)
    last_lines: list[str] = field(default_factory=list)
    hidden: int = 0
    headline: str | None = None
    errors: int = 0
    warnings: int = 0
    total_lines: int = 0
    truncated: bool = False


# ---------------------------------------------------------------------------
# Headline detectors
# ---------------------------------------------------------------------------
# Each detector is a (regex, formatter) pair. ``formatter`` receives the
# match object and returns the headline string, or None to decline.

_PYTEST_VERDICT = re.compile(
    r"=+\s*"
    r"(?:(?P<failed>\d+)\s+failed[^,=]*)?"
    r"(?:,?\s*(?P<passed>\d+)\s+passed[^,=]*)?"
    r"(?:,?\s*(?P<skipped>\d+)\s+skipped[^,=]*)?"
    r"(?:,?\s*(?P<errors>\d+)\s+errors?[^,=]*)?"
    r"(?:,?\s*(?P<warnings>\d+)\s+warnings?[^,=]*)?"
    r".*\sin\s[\d.]+s"
)

_JEST_VERDICT = re.compile(
    r"Tests?:\s+"
    r"(?:(?P<failed>\d+)\s+failed,\s+)?"
    r"(?:(?P<skipped>\d+)\s+skipped,\s+)?"
    r"(?P<passed>\d+)\s+passed",
)

_VITEST_VERDICT = re.compile(
    r"Test Files\s+(?P<files>\d+)\s+(?:passed|failed)",
)

_BUILD_OK = re.compile(
    r"(?:✓|built|finished|compiled successfully|build complete|done in [\d.]+s)",
    re.IGNORECASE,
)

_BUILD_FAIL = re.compile(
    r"(?:✘|failed with \d+ errors?|build failed|exited with code [1-9]|"
    r"command failed)",
    re.IGNORECASE,
)


def _detect_pytest(text: str) -> str | None:
    # Pytest prints the verdict on a line of "=" signs near the end.
    for line in reversed(text.splitlines()[-30:]):
        m = _PYTEST_VERDICT.search(line)
        if not m:
            continue
        bits: list[str] = []
        passed = m.group("passed")
        failed = m.group("failed")
        skipped = m.group("skipped")
        errors = m.group("errors")
        warns = m.group("warnings")
        if failed:
            bits.append(f"{failed} failed")
        if passed:
            bits.append(f"{passed} passed")
        if errors:
            bits.append(f"{errors} errors")
        if skipped:
            bits.append(f"{skipped} skipped")
        if warns:
            bits.append(f"{warns} warnings")
        if bits:
            verdict = ", ".join(bits)
            return f"pytest · {verdict}"
    return None


def _detect_jest(text: str) -> str | None:
    for line in reversed(text.splitlines()[-30:]):
        m = _JEST_VERDICT.search(line)
        if not m:
            continue
        bits: list[str] = []
        if m.group("failed"):
            bits.append(f"{m.group('failed')} failed")
        bits.append(f"{m.group('passed')} passed")
        if m.group("skipped"):
            bits.append(f"{m.group('skipped')} skipped")
        return "jest · " + ", ".join(bits)
    return None


def _detect_vitest(text: str) -> str | None:
    m = _VITEST_VERDICT.search(text)
    if not m:
        return None
    return f"vitest · {m.group('files')} files"


def _detect_build_verdict(text: str) -> str | None:
    tail = "\n".join(text.splitlines()[-40:])
    if _BUILD_FAIL.search(tail):
        return "build · failed"
    if _BUILD_OK.search(tail):
        return "build · ok"
    return None


_DETECTORS = (
    _detect_pytest,
    _detect_jest,
    _detect_vitest,
    _detect_build_verdict,
)


def _detect_headline(text: str) -> str | None:
    for det in _DETECTORS:
        try:
            head = det(text)
        except Exception:
            continue
        if head:
            return head
    return None


# ---------------------------------------------------------------------------
# Severity counting
# ---------------------------------------------------------------------------

# Severity detection — alternatives without trailing word-boundary so
# patterns ending in non-word chars (``error:``) still match. We anchor
# at line start or whitespace so the patterns don't fire on substrings
# like "miscellaneous" or "warningless".
_ERROR_PATTERNS = re.compile(
    r"(?:^|\s)(?:"
    r"ERROR\b"            # generic uppercase
    r"|FAILED\b"
    r"|Traceback\b"
    r"|fatal:"
    r"|error\s+TS\d+"     # tsc
    r"|error:"            # gcc / rust / generic
    r"|✘"                 # vite/rollup
    r")",
    re.MULTILINE,
)

_WARN_PATTERNS = re.compile(
    r"(?:^|\s)(?:"
    r"WARN(?:ING)?\b"
    r"|warning:"
    r"|⚠"
    r")",
    re.MULTILINE | re.IGNORECASE,
)


def _count_severities(text: str) -> tuple[int, int]:
    return (
        len(_ERROR_PATTERNS.findall(text)),
        len(_WARN_PATTERNS.findall(text)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize_output(
    text: str,
    *,
    head: int = 8,
    tail: int = 16,
    threshold: int = 30,
) -> OutputSummary:
    """Compress ``text`` for terminal display.

    - ``head``/``tail``: lines preserved at top and bottom when trimming.
    - ``threshold``: total lines above which we apply the head+tail trim.
      Below the threshold, the whole output is returned untruncated.
    """
    if not text:
        return OutputSummary()

    # Drop trailing blank lines — they make the tail look ragged.
    lines = text.rstrip().splitlines()
    total = len(lines)

    summary = OutputSummary(total_lines=total)
    summary.headline = _detect_headline(text)
    summary.errors, summary.warnings = _count_severities(text)

    if total <= threshold:
        summary.first_lines = lines
        return summary

    summary.first_lines = lines[:head]
    summary.last_lines = lines[-tail:]
    summary.hidden = total - head - tail
    summary.truncated = True
    return summary


def summarize_for_model(text: str, budget: int = 3000) -> str:
    """Return a model-friendly version of long output.

    The summarizer for terminal display is for humans. The tool result
    we pass back to the LLM is a separate concern — it should preserve
    enough context for follow-up reasoning without blowing the context
    window. We keep head + tail and drop the middle, with a marker so
    the model knows lines were elided.
    """
    if len(text) <= budget:
        return text
    half = budget // 2
    dropped = len(text) - 2 * half
    return f"{text[:half]}\n...[truncated {dropped} chars of middle output]...\n{text[-half:]}"
