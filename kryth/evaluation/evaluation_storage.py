"""Persists EvaluationRun and EvaluationResult objects to JSON files."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .evaluation_metrics import (
    EvaluationResult,
    EvaluationRun,
    EvaluationScores,
    ReviewScore,
    RuleViolation,
    RuleReward,
)


DEFAULT_EVAL_HISTORY_DIR = str(
    (Path(__file__).parent.parent / "evaluation_history").resolve()
)


def _run_id() -> str:
    return f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


# ── Save ──────────────────────────────────────────────────────────────────────

def save_evaluation_run(
    run: EvaluationRun,
    history_dir: str = DEFAULT_EVAL_HISTORY_DIR,
) -> str:
    _ensure_dir(history_dir)
    if not run.run_id:
        run.run_id = _run_id()
    if not run.timestamp:
        run.timestamp = datetime.now(timezone.utc).isoformat()
    path = os.path.join(history_dir, f"{run.run_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run.to_dict(), fh, indent=2, default=str)
    return path


def save_evaluation_result(
    result: EvaluationResult,
    history_dir: str = DEFAULT_EVAL_HISTORY_DIR,
) -> str:
    _ensure_dir(history_dir)
    fname = f"{result.mission_id}_{result.timestamp[:10]}.json"
    path = os.path.join(history_dir, fname)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, default=str)
    return path


# ── Load ──────────────────────────────────────────────────────────────────────

def load_evaluation_run(path: str) -> EvaluationRun:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    run = EvaluationRun(
        run_id=raw.get("run_id", ""),
        benchmark_run_id=raw.get("benchmark_run_id", ""),
        timestamp=raw.get("timestamp", ""),
        kryth_version=raw.get("kryth_version", "unknown"),
    )
    for r_raw in raw.get("results", []):
        run.results.append(_load_result(r_raw))
    return run


def list_evaluation_runs(history_dir: str = DEFAULT_EVAL_HISTORY_DIR) -> list[str]:
    d = Path(history_dir)
    if not d.exists():
        return []
    return sorted(str(p) for p in d.glob("eval_*.json"))


def load_latest_evaluation_run(
    history_dir: str = DEFAULT_EVAL_HISTORY_DIR,
) -> Optional[EvaluationRun]:
    runs = list_evaluation_runs(history_dir)
    if not runs:
        return None
    return load_evaluation_run(runs[-1])


# ── Internal deserialization ──────────────────────────────────────────────────

def _load_scores(d: dict) -> EvaluationScores:
    s = EvaluationScores()
    for field in (
        "correctness", "code_quality", "architecture", "testing",
        "performance", "security", "maintainability", "documentation",
        "parallel_efficiency", "memory_reuse", "recovery_quality",
        "build_success", "test_success", "overall",
    ):
        if field in d:
            setattr(s, field, int(d[field]))
    return s


def _load_review_score(d: dict) -> ReviewScore:
    return ReviewScore(
        dimension=d.get("dimension", ""),
        score=int(d.get("score", 0)),
        weight=float(d.get("weight", 1.0)),
        findings=list(d.get("findings", [])),
        suggestions=list(d.get("suggestions", [])),
        reviewer=d.get("reviewer", "static"),
        duration_s=float(d.get("duration_s", 0.0)),
    )


def _load_result(d: dict) -> EvaluationResult:
    result = EvaluationResult(
        mission_id=d.get("mission_id", ""),
        mission_name=d.get("mission_name", ""),
        workspace=d.get("workspace", ""),
        timestamp=d.get("timestamp", ""),
        run_id=d.get("run_id", ""),
        evaluation_duration_s=float(d.get("evaluation_duration_s", 0.0)),
        error=d.get("error", ""),
    )
    result.scores = _load_scores(d.get("scores", {}))
    result.review_scores = {
        k: _load_review_score(v)
        for k, v in d.get("review_scores", {}).items()
    }
    result.violations = [
        RuleViolation(
            rule_id=v.get("rule_id", ""),
            severity=v.get("severity", "info"),
            file=v.get("file", ""),
            line=int(v.get("line", 1)),
            message=v.get("message", ""),
            penalty=int(v.get("penalty", 0)),
        )
        for v in d.get("violations", [])
    ]
    result.rewards = [
        RuleReward(
            rule_id=r.get("rule_id", ""),
            file=r.get("file", ""),
            message=r.get("message", ""),
            bonus=int(r.get("bonus", 0)),
        )
        for r in d.get("rewards", [])
    ]
    result.files_evaluated = list(d.get("files_evaluated", []))
    return result
