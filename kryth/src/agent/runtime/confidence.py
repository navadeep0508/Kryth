"""Confidence scoring and policy decision-making for KRYTH scratchpad.

Three confidence domains:
  Knowledge   — how well the agent understands the codebase
  Diagnosis   — how certain the hypothesis/fix direction is
  Completion  — how confident the task is actually done

PolicyController gates all major decisions using these scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ── Thresholds (tunable constants) ───────────────────────────────────────

KNOWLEDGE_READ_THRESHOLD = 0.85     # stop reading when repo understanding ≥ 85%
DIAGNOSIS_CONCLUDE_THRESHOLD = 0.80 # conclude diagnosis at 80% certainty
COMPLETION_DONE_THRESHOLD = 0.90    # declare done at 90% completion confidence
VERIFY_THRESHOLD = 0.70             # trigger verification at 70%


# ── Confidence scores ────────────────────────────────────────────────────

@dataclass
class ConfidenceScores:
    """Multi-dimensional confidence that drives agent decisions.

    Every field is 0.0–1.0.
    Derived properties aggregate domains into higher-level scores.
    """

    # ── Knowledge: repo understanding ──────────────────────────────────
    repo_scan_done: float = 0.0       # project structure scanned
    entrypoint_read: float = 0.0      # main entrypoint(s) read
    core_modules_read: float = 0.0    # core/dependency modules read
    file_coverage: float = 0.0        # fraction of relevant files read

    # ── Diagnosis: root-cause / fix certainty ──────────────────────────
    stacktrace_found: float = 0.0     # error trace located
    root_cause: float = 0.0           # root cause identified
    hypothesis_tested: float = 0.0    # fix hypothesis was applied

    # ── Completion: task-done certainty ────────────────────────────────
    todos_complete: float = 0.0       # fraction of plan steps done
    tests_passed: float = 0.0         # tests green
    verification_done: float = 0.0    # manual / integration verify done

    # ── Derived ────────────────────────────────────────────────────────

    @property
    def knowledge(self) -> float:
        return (self.repo_scan_done + self.entrypoint_read
                + self.core_modules_read + self.file_coverage) / 4.0

    @property
    def diagnosis(self) -> float:
        return (self.stacktrace_found + self.root_cause
                + self.hypothesis_tested) / 3.0

    @property
    def completion(self) -> float:
        return (self.todos_complete + self.tests_passed
                + self.verification_done) / 3.0

    @property
    def overall(self) -> float:
        """Single blended score (used for scratchpad.confidence backward compat)."""
        return (self.knowledge + self.diagnosis + self.completion) / 3.0


# ── Policy controller ────────────────────────────────────────────────────

class PolicyController:
    """Gate decisions on confidence thresholds.

    Each method answers a single yes/no policy question.
    Callers (ScratchpadManager methods) use these to replace raw heuristics.
    """

    @staticmethod
    def should_read_more(scores: ConfidenceScores) -> bool:
        """Keep reading until codebase understanding is solid."""
        return scores.knowledge < KNOWLEDGE_READ_THRESHOLD

    @staticmethod
    def should_conclude(scores: ConfidenceScores) -> bool:
        """Confident enough to conclude diagnosis and act."""
        return scores.diagnosis >= DIAGNOSIS_CONCLUDE_THRESHOLD

    @staticmethod
    def is_task_complete(scores: ConfidenceScores) -> bool:
        """Task is done when completion confidence is high."""
        return scores.completion >= COMPLETION_DONE_THRESHOLD

    @staticmethod
    def should_ask_user(scores: ConfidenceScores, blockers: List[str]) -> bool:
        """Ask the user when stuck AND diagnosis is uncertain."""
        return len(blockers) > 0 and scores.diagnosis < 0.5

    @staticmethod
    def should_verify(scores: ConfidenceScores, files_written: int) -> bool:
        """Trigger verification after writes if completion is still uncertain."""
        return files_written > 0 and scores.completion < VERIFY_THRESHOLD


# ── Score updater (derives scores from scratchpad state) ──────────────────

def update_scores_from_state(
    scores: ConfidenceScores,
    *,
    completed_steps: List[str],
    findings: List[str],
    todos: List,
    files_written: int,
    tests_passed: bool,
    tool_error_count: int,
    tool_call_count: int,
    loop_count: int,
    force_summarize: bool,
) -> None:
    """Derive all confidence fields from the current scratchpad state.

    This is called every ``_recompute_state()`` — scores are a derived
    view of accumulated evidence, not event-driven counters.
    """
    steps_str = " ".join(completed_steps).lower()
    findings_str = " ".join(findings).lower()

    # ── Knowledge ──────────────────────────────────────────────────────
    scores.repo_scan_done = 1.0 if "scanned" in steps_str else 0.0

    read_paths = [s for s in completed_steps if s.startswith("read ")]
    unique_reads = len(set(read_paths)) if read_paths else 0
    entrypoints = {"main.py", "app.py", "index.js", "index.ts", "cli.py", "setup.py"}
    entrypoint_hits = sum(1 for p in read_paths if any(
        ep in p.split("|")[0].strip().lower() for ep in entrypoints
    ))
    scores.entrypoint_read = min(entrypoint_hits / 2.0, 1.0)

    # Assume ~5 core files needed for solid understanding
    scores.core_modules_read = min(unique_reads / 5.0, 1.0)

    # file_coverage = fraction of core reads we've done (5 reads = full)
    # Also bump for each read beyond 5
    coverage = min(unique_reads / 5.0, 1.0)
    if unique_reads >= 5:
        coverage += min((unique_reads - 5) * 0.05, 0.2)
    scores.file_coverage = min(coverage, 1.0)

    # ── Diagnosis ──────────────────────────────────────────────────────
    scores.stacktrace_found = 1.0 if any(
        w in findings_str for w in ("error", "trace", "traceback", "failed")
    ) else 0.0

    scores.root_cause = 1.0 if any(
        w in findings_str for w in ("root cause", "because", "caused by", "origin")
    ) else 0.0

    scores.hypothesis_tested = 1.0 if files_written > 0 else 0.0

    # ── Completion ─────────────────────────────────────────────────────
    if todos:
        done = sum(1 for t in todos if t.status == "completed")
        scores.todos_complete = done / max(len(todos), 1)
    else:
        scores.todos_complete = 0.0

    scores.tests_passed = 1.0 if tests_passed else 0.0
    scores.verification_done = 1.0 if force_summarize else 0.0

    # Penalty for high error rates (reduces overall confidence)
    if tool_call_count > 0:
        err_rate = tool_error_count / max(tool_call_count, 1)
        if err_rate > 0.3:
            # Reduce all domains proportionally
            penalty = min(err_rate, 1.0) * 0.3
            scores.stacktrace_found = max(scores.stacktrace_found - penalty, 0.0)
            scores.root_cause = max(scores.root_cause - penalty, 0.0)

    # Penalty for loops (reduces diagnosis confidence)
    if loop_count > 2:
        scores.root_cause = max(scores.root_cause - 0.2, 0.0)
        scores.hypothesis_tested = max(scores.hypothesis_tested - 0.2, 0.0)
