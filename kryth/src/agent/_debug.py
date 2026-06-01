"""Autonomous-debug helper tool.

Lets the agent ask a cheap reviewer model to triage a failed command —
"what's broken and what should I try next" — without burning the main
model's context on long error traces. The agent stays in the driver's
seat: this tool gives a diagnosis, the agent decides what to do.
"""

from __future__ import annotations

from agent.tools._results import err


def diagnose_error(
    command,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
    context_hint: str = "",
):
    """Diagnose a failed command. Returns the model's CAUSE/FIX/CONFIDENCE
    block, or a structured error if inputs are unusable.

    ``command``         — the literal command that ran (string).
    ``stdout/stderr``   — captured output; tails are kept, head dropped.
    ``exit_code``       — the non-zero exit code.
    ``context_hint``    — optional one-liner: what the agent was trying
                          to accomplish, so the diagnosis isn't context-free.
    """
    if not isinstance(command, str) or not command.strip():
        return err("BAD_ARGS", "diagnose_error: command must be a non-empty string")

    try:
        ec = int(exit_code)
    except (TypeError, ValueError):
        return err("BAD_ARGS", "diagnose_error: exit_code must be an integer")

    if ec == 0 and not (stderr or "").strip():
        return "(command appears to have succeeded — nothing to diagnose)"

    from agent.llm import diagnose_error as _ask

    findings = _ask(
        command=command.strip(),
        stdout=str(stdout or ""),
        stderr=str(stderr or ""),
        exit_code=ec,
        context_hint=str(context_hint or ""),
    )
    if not findings:
        return "(diagnosis unavailable — proceed manually)"
    return findings


__all__ = ["diagnose_error"]
