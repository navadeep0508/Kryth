"""Pre-response self-evaluation gate for KRYTH.

Before declaring a non-trivial task "done", this module scores confidence
across four dimensions and decides whether an extra validation turn is needed.

Scoring (each 0.0–1.0):
  files_written   — did we create/edit the expected files?
  run_succeeded   — did at least one run_command succeed?
  task_complete   — does the last assistant message address the goal?
  no_errors       — are there recent unresolved [ERROR] tool results?

Overall confidence = weighted average. If below CONF_THRESHOLD (default 0.6),
inject a validation nudge into the session so the model does one more check.

Usage:
    from agent.self_eval import evaluate_task, SelfEvalResult
    ev = evaluate_task(session, task_description, task_complexity)
    if not ev.confident:
        session.append({"role": "user", "content": ev.nudge_message()})
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

CONF_THRESHOLD = float(__import__("os").environ.get("KRYTH_SELF_EVAL_THRESHOLD", "0.60"))


@dataclass
class SelfEvalResult:
    confidence: float
    files_score: float
    run_score: float
    task_score: float
    error_score: float
    notes: List[str] = field(default_factory=list)

    @property
    def confident(self) -> bool:
        return self.confidence >= CONF_THRESHOLD

    def nudge_message(self) -> str:
        lines = [f"[sys] Self-eval confidence {self.confidence:.0%} < threshold."]
        for note in self.notes[:4]:
            lines.append(f"  - {note}")
        lines.append("Verify the task is complete. Fix any outstanding issues.")
        return "\n".join(lines)

    def summary(self) -> str:
        return (
            f"confidence={self.confidence:.0%} "
            f"(files={self.files_score:.0%}, run={self.run_score:.0%}, "
            f"task={self.task_score:.0%}, no_errors={self.error_score:.0%})"
        )


def evaluate_task(
    session,
    task_description: str = "",
    task_complexity: str = "medium",
) -> SelfEvalResult:
    """Score confidence across four dimensions from the session message history."""
    msgs = getattr(session, "messages", [])
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]

    # ── Dimension 1: files written ────────────────────────────────────────────
    write_tools = {"write_file", "edit_file", "multi_edit"}
    successful_writes = [
        m for m in tool_msgs
        if m.get("name") in write_tools
        and not (m.get("content") or "").startswith("[ERROR ")
    ]
    files_score = min(1.0, len(successful_writes) / max(1, _expected_file_count(task_description)))
    files_notes = []
    if not successful_writes:
        files_notes.append("No files were written (expected at least 1).")

    # ── Dimension 2: run_command succeeded ───────────────────────────────────
    run_msgs = [m for m in tool_msgs if m.get("name") == "run_command"]
    ok_runs = [
        m for m in run_msgs
        if not (m.get("content") or "").startswith("[ERROR ")
        and "exit_code" not in (m.get("content") or "")  # simplified check
    ]
    run_score: float
    if not run_msgs:
        # No run attempted — ok for static files, penalise for executable tasks
        run_score = 0.8 if _is_static_task(task_description) else 0.3
        if not _is_static_task(task_description):
            files_notes.append("No run_command executed — task may not be verified.")
    else:
        run_score = len(ok_runs) / len(run_msgs)
        if run_score < 0.5:
            files_notes.append(f"{len(run_msgs) - len(ok_runs)}/{len(run_msgs)} run commands failed.")

    # ── Dimension 3: recent unresolved errors ────────────────────────────────
    recent_tool = tool_msgs[-6:]
    error_count = sum(
        1 for m in recent_tool
        if (m.get("content") or "").startswith("[ERROR ")
    )
    error_score = max(0.0, 1.0 - error_count * 0.25)
    if error_count:
        files_notes.append(f"{error_count} recent [ERROR] tool result(s) in last 6 turns.")

    # ── Dimension 4: task completion heuristic ────────────────────────────────
    last_assistant = next(
        (m.get("content", "") for m in reversed(assistant_msgs) if m.get("content")),
        "",
    )
    task_score = _score_task_completion(last_assistant, task_description)
    if task_score < 0.5:
        files_notes.append("Last assistant message doesn't clearly address the task.")

    # ── Weights by complexity ─────────────────────────────────────────────────
    weights = {
        "simple":  (0.5, 0.2, 0.1, 0.2),
        "medium":  (0.3, 0.3, 0.2, 0.2),
        "complex": (0.25, 0.25, 0.3, 0.2),
    }.get(task_complexity, (0.3, 0.3, 0.2, 0.2))

    confidence = (
        weights[0] * files_score +
        weights[1] * run_score +
        weights[2] * task_score +
        weights[3] * error_score
    )

    return SelfEvalResult(
        confidence=round(confidence, 3),
        files_score=round(files_score, 3),
        run_score=round(run_score, 3),
        task_score=round(task_score, 3),
        error_score=round(error_score, 3),
        notes=files_notes,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _expected_file_count(task: str) -> int:
    """Rough heuristic: how many files does this task probably need?"""
    task = task.lower()
    if any(w in task for w in ("scaffold", "project", "app", "full-stack", "frontend", "backend")):
        return 3
    if any(w in task for w in ("refactor", "rename all", "update all")):
        return 2
    return 1


def _is_static_task(task: str) -> bool:
    task = task.lower()
    return bool(re.search(r"\.(yaml|yml|json|toml|cfg|ini|md|txt|env|lock|csv)\b", task))


def _score_task_completion(assistant_text: str, task: str) -> float:
    """Simple heuristic: does the assistant text mention key task nouns?"""
    if not assistant_text or not task:
        return 0.5
    task_words = set(re.findall(r"\b\w{4,}\b", task.lower()))
    resp_words = set(re.findall(r"\b\w{4,}\b", assistant_text.lower()))
    if not task_words:
        return 0.7
    overlap = len(task_words & resp_words) / len(task_words)
    # Boost if response contains success indicators
    success_indicators = {"done", "created", "written", "complete", "success", "finished"}
    if success_indicators & resp_words:
        overlap = min(1.0, overlap + 0.2)
    return min(1.0, overlap * 1.5)
