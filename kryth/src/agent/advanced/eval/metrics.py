"""Metrics for the KRYTH benchmark harness."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class TaskMetrics:
    task_id: str
    success: bool = False
    compile_ok: bool = False
    tests_pass: bool = False
    patch_correct: bool = False
    retries: int = 0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_s: float = 0.0
    errors: List[str] = field(default_factory=list)
    output_summary: str = ""

    def score(self) -> float:
        """0–1 composite quality score."""
        s = 0.0
        if self.success:        s += 0.40
        if self.compile_ok:     s += 0.20
        if self.tests_pass:     s += 0.25
        if self.patch_correct:  s += 0.15
        # Penalty for excessive retries (each retry after 1 costs 2%)
        s -= max(0, self.retries - 1) * 0.02
        return max(0.0, min(1.0, s))

    def to_dict(self) -> dict:
        return {
            "task_id":        self.task_id,
            "success":        self.success,
            "compile_ok":     self.compile_ok,
            "tests_pass":     self.tests_pass,
            "patch_correct":  self.patch_correct,
            "retries":        self.retries,
            "llm_calls":      self.llm_calls,
            "tokens_in":      self.tokens_in,
            "tokens_out":     self.tokens_out,
            "duration_s":     round(self.duration_s, 2),
            "score":          round(self.score(), 3),
            "errors":         self.errors,
            "output_summary": self.output_summary[:200],
        }


def aggregate(results: List[TaskMetrics]) -> dict:
    if not results:
        return {}
    n = len(results)
    return {
        "tasks":           n,
        "success_rate":    round(sum(r.success for r in results) / n, 3),
        "compile_rate":    round(sum(r.compile_ok for r in results) / n, 3),
        "test_pass_rate":  round(sum(r.tests_pass for r in results) / n, 3),
        "avg_score":       round(sum(r.score() for r in results) / n, 3),
        "avg_retries":     round(sum(r.retries for r in results) / n, 2),
        "avg_llm_calls":   round(sum(r.llm_calls for r in results) / n, 2),
        "avg_tokens_in":   round(sum(r.tokens_in for r in results) / n),
        "avg_duration_s":  round(sum(r.duration_s for r in results) / n, 2),
    }
