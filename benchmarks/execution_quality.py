"""Execution benchmark catalog and metrics over explicitly executed samples."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


BENCHMARK_CASES = (
    "browser-navigation",
    "browser-search",
    "browser-multi-tab",
    "browser-recovery",
    "file-operations",
    "code-editing",
    "terminal-execution",
    "test-execution",
    "context-overflow",
    "multi-step-recovery",
)


@dataclass(frozen=True)
class BenchmarkSample:
    case_id: str
    executed: bool
    objective_met: bool | None = None
    verification_correct: bool | None = None
    recovery_attempted: bool = False
    recovery_succeeded: bool | None = None
    duplicate_actions: int = 0
    false_completion: bool = False
    tokens: int | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_metrics(samples: Iterable[BenchmarkSample]) -> dict:
    executed = [sample for sample in samples if sample.executed]
    if not executed:
        return {
            "executed_cases": 0,
            "task_completion_rate": None,
            "verification_accuracy": None,
            "recovery_success_rate": None,
            "duplicate_action_rate": None,
            "false_completion_rate": None,
            "total_tokens": None,
            "average_execution_seconds": None,
        }

    completion = [sample.objective_met for sample in executed if sample.objective_met is not None]
    verification = [sample.verification_correct for sample in executed if sample.verification_correct is not None]
    recoveries = [sample for sample in executed if sample.recovery_attempted]
    token_values = [sample.tokens for sample in executed if sample.tokens is not None]
    durations = [sample.duration_seconds for sample in executed if sample.duration_seconds is not None]
    return {
        "executed_cases": len(executed),
        "task_completion_rate": sum(completion) / len(completion) if completion else None,
        "verification_accuracy": sum(verification) / len(verification) if verification else None,
        "recovery_success_rate": (
            sum(sample.recovery_succeeded is True for sample in recoveries) / len(recoveries)
            if recoveries else None
        ),
        "duplicate_action_rate": sum(sample.duplicate_actions > 0 for sample in executed) / len(executed),
        "false_completion_rate": sum(sample.false_completion for sample in executed) / len(executed),
        "total_tokens": sum(token_values) if token_values else None,
        "average_execution_seconds": sum(durations) / len(durations) if durations else None,
    }
