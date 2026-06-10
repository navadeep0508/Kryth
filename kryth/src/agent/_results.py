"""Stable tool result / error convention.

The model receives a tool's return string verbatim. To reliably tell
success from failure (without parsing prose), errors carry a fixed
prefix and a short machine-readable code:

    [ERROR <CODE>] <human-readable message>
    <optional details on subsequent lines>

Successes have NO prefix — they are the raw payload (file content,
search hits, diff text, etc.) so the model can consume them directly.

Codes (closed set so the model can learn them):

    NOT_FOUND          missing file/symbol/task
    BAD_ARGS           wrong shape, missing key, unparseable JSON
    EXEC_FAILED        runtime failure inside the tool
    NON_ZERO_EXIT      shell command returned a non-zero exit
    TIMEOUT            operation exceeded its time budget
    AMBIGUOUS          edit/multi-edit pattern matched 0 or >1 places
    PERMISSION_DENIED  user declined an ask
    HOOK_BLOCKED       PreToolUse hook refused
    INVALID_STATE      tool not allowed in current agent state
    TOOL_NOT_FOUND     unknown tool name
    UNSUPPORTED        operation not supported on this platform/path

Callers should use ``err(...)`` and never hand-craft the prefix, so
the format stays consistent. ``is_error`` / ``error_code`` are the
reverse direction.
"""

from __future__ import annotations

from typing import Final


TOOL_ERROR_PREFIX: Final = "[ERROR "
# Closing bracket of the code marker, used by parsers to find the
# split between code and message.
_CODE_END: Final = "]"


# Closed code set. Keep names short, uppercase, snake_case-free.
ErrorCode = str  # alias for documentation; runtime is a plain str
CODES: Final = frozenset({
    "NOT_FOUND",
    "BAD_ARGS",
    "EXEC_FAILED",
    "NON_ZERO_EXIT",
    "TIMEOUT",
    "AMBIGUOUS",
    "PERMISSION_DENIED",
    "HOOK_BLOCKED",
    "INVALID_STATE",
    "TOOL_NOT_FOUND",
    "UNSUPPORTED",
})


def err(code: ErrorCode, message: str, details: str = "") -> str:
    """Format a tool error. ``code`` must be one of ``CODES``; passing
    an unknown code is a programming bug, not a user-facing failure, so
    we tolerate it (formatting still works) but it's the caller's fault."""
    if not message:
        message = code.lower().replace("_", " ")
    body = f"{TOOL_ERROR_PREFIX}{code}{_CODE_END} {message}"
    if details:
        return body + "\n" + details
    return body


def is_error(result: str) -> bool:
    """True when ``result`` is a tool error formatted by ``err``."""
    return isinstance(result, str) and result.startswith(TOOL_ERROR_PREFIX)


def error_code(result: str) -> str | None:
    """Pull the error code back out of a result. Returns ``None`` if the
    result isn't an error or the prefix is malformed."""
    if not is_error(result):
        return None
    end = result.find(_CODE_END, len(TOOL_ERROR_PREFIX))
    if end < 0:
        return None
    return result[len(TOOL_ERROR_PREFIX):end]


__all__ = ["TOOL_ERROR_PREFIX", "CODES", "err", "is_error", "error_code"]
