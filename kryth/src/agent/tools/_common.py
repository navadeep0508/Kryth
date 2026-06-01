"""Shared helpers used across tool implementations."""

from __future__ import annotations

from agent.tools._results import TOOL_ERROR_PREFIX, err


# Legacy alias. New code should call ``err("NON_ZERO_EXIT", ...)`` from
# ``_results``; this name remains so the rest of the codebase (and any
# saved transcripts) can still detect "a tool error happened" by
# substring match. Both the new ``[ERROR ...]`` prefix and historical
# ``[NON_ZERO_EXIT]`` results match it.
RUN_COMMAND_ERROR_MARKER = TOOL_ERROR_PREFIX

# Max chars stored for a single tool result. Head + tail are preserved
# so both setup context and error tails (stack traces, last-test-failure
# line) survive trimming. read_file is exempt — it has its own
# offset/limit pagination.
TOOL_OUTPUT_BUDGET = 3000


def trim_head_tail(text: str, budget: int = TOOL_OUTPUT_BUDGET) -> str:
    """Trim long output to head + tail with an elision marker.

    Returns ``text`` unchanged when under budget. Otherwise keeps the
    first ``budget // 2`` chars and the last ``budget // 2`` chars,
    joined by a single-line marker showing how much was dropped.
    """
    if len(text) <= budget:
        return text
    half = budget // 2
    dropped = len(text) - 2 * half
    head = text[:half]
    tail = text[-half:]
    return f"{head}\n...[truncated {dropped} chars]...\n{tail}"


__all__ = [
    "RUN_COMMAND_ERROR_MARKER",
    "TOOL_ERROR_PREFIX",
    "TOOL_OUTPUT_BUDGET",
    "err",
    "trim_head_tail",
]
