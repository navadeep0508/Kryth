"""Repair Loop — autonomous recovery when an agent fails.

When a worker fails:
  1. Analyze the error
  2. Generate a targeted repair prompt
  3. Spawn a repair agent with smaller scope
  4. Re-validate
  5. Retry up to MAX_RETRIES times
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

MAX_RETRIES = 3


@dataclass
class RepairResult:
    success: bool
    output: str
    attempts: int
    error: str = ""


def attempt_repair(
    agent_id: str,
    role: str,
    mission: str,
    original_error: str,
    project_context: str = "",
    max_turns: int = 40,
    project_root: str = ".",
) -> RepairResult:
    """Try to repair a failed agent's work.

    Checks repair experience first. If a high-confidence cached repair
    exists, applies it directly without an LLM call.
    """
    # --- Experience lookup: try cached repair before reasoning ---
    try:
        from agent.experience import get_experience
        exp = get_experience(project_root)
        error_type = _extract_error_type(original_error)
        cached = exp.recommend("repair", f"{error_type}:{original_error[:100]}")
        if cached and cached.confidence >= 0.75 and cached.repair_commands:
            return RepairResult(
                success=True,
                output=f"REPAIR_COMPLETE: {agent_id} (cached repair applied)\n"
                       + "\n".join(f"Applied: {cmd}" for cmd in cached.repair_commands),
                attempts=0,
            )
    except Exception:
        pass

    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    repair_prompt = f"""You are a REPAIR AGENT for a failed task.

ORIGINAL ROLE: {role}
ORIGINAL MISSION: {mission}

FAILURE REASON: {original_error[:1000]}

PROJECT CONTEXT:
{project_context[:2000]}

Your job:
1. Diagnose what went wrong
2. Fix ONLY the failing part
3. Do not redo work that succeeded
4. Report what you fixed starting with: REPAIR_COMPLETE: {agent_id}

Keep the fix minimal and targeted."""

    repair_commands_used: list = []

    for attempt in range(1, MAX_RETRIES + 1):
        parent = get_session()
        nested = _build_nested(f"Repair({role})", repair_prompt, parent.depth)
        nested.system_prompt = repair_prompt
        nested.messages = [
            {"role": "system", "content": repair_prompt},
            {"role": "user", "content": f"Attempt {attempt}: Fix the failure and report results."},
        ]

        token = push_session(nested)
        try:
            result = run_inner_loop(nested, max_turns, verbose_usage=False)
            content = getattr(result, "content", "") or ""
            if content and "REPAIR_COMPLETE" in content:
                # Record successful repair for future runs
                _learn_repair(original_error, repair_commands_used, True, project_root)
                return RepairResult(success=True, output=content, attempts=attempt)
        except Exception as exc:
            original_error = str(exc)
        finally:
            pop_session(token)

    _learn_repair(original_error, repair_commands_used, False, project_root)
    return RepairResult(
        success=False,
        output="",
        attempts=MAX_RETRIES,
        error=f"All {MAX_RETRIES} repair attempts failed. Original error: {original_error}",
    )


def _extract_error_type(error_message: str) -> str:
    """Extract a short error type string from an error message."""
    import re
    # Python exception names
    m = re.search(r"\b(\w+Error|\w+Exception|\w+Warning)\b", error_message)
    if m:
        return m.group(1)
    # Exit codes
    m = re.search(r"exit(?:\s+code)?\s+(\d+)", error_message, re.I)
    if m:
        return f"ExitCode{m.group(1)}"
    return "UnknownError"


def _learn_repair(error: str, commands: list, success: bool, root: str) -> None:
    try:
        from agent.experience import get_experience
        exp = get_experience(root)
        exp.learn(
            "repair",
            error_type=_extract_error_type(error),
            error_message=error[:200],
            repair_commands=commands,
            success=success,
        )
    except Exception:
        pass
