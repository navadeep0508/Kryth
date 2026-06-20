"""Orchestrates the full evaluation pipeline for a single mission workspace."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .evaluation_metrics import (
    EvaluationResult,
    EvaluationScores,
    RuleViolation,
    RuleReward,
    ReviewScore,
)
from .quality_rules import analyze_workspace, security_score_from_violations
from .evaluator_agents import run_all_reviewers


def _clamp(v: float) -> int:
    return max(0, min(100, int(round(v))))


def _score_correctness(
    mission_passed: bool,
    build_success: bool = True,
    test_success: bool = True,
) -> int:
    base = 100 if mission_passed else 0
    if not build_success:
        base = max(base - 30, 0)
    if not test_success:
        base = max(base - 20, 0)
    return base


def _score_memory_reuse(
    speculative_files: int,
    worker_pool_files: int,
    experience_hits: int,
) -> int:
    """Heuristic: more preloading = better memory reuse score."""
    total = speculative_files + worker_pool_files + experience_hits * 5
    if total == 0:
        return 20
    return _clamp(20 + min(total * 3, 80))


def _score_recovery(
    failures_injected: int,
    auto_recoveries: int,
    human_interventions: int,
) -> int:
    if failures_injected == 0:
        return 75  # no failures to recover from — neutral score
    if auto_recoveries + human_interventions == 0:
        return 10
    recovery_rate = auto_recoveries / failures_injected
    autonomy_bonus = 20 if human_interventions == 0 else -10
    return _clamp(int(recovery_rate * 80) + autonomy_bonus)


def _merge_llm_static(
    static_score: int,
    review_scores: dict[str, ReviewScore],
    dimension: str,
) -> int:
    """Merge LLM reviewer score with static score (LLM takes precedence if available)."""
    rs = review_scores.get(dimension)
    if rs is None or rs.reviewer in ("error",):
        return static_score
    if rs.reviewer == "llm":
        return rs.score
    if rs.reviewer == "hybrid":
        return _clamp((static_score + rs.score) // 2)
    return static_score  # reviewer="static" → we already have it


# ── Main evaluation entry point ───────────────────────────────────────────────

def evaluate_mission(
    workspace: str,
    mission_id: str = "",
    mission_name: str = "",
    run_id: str = "",
    mission_passed: bool = False,
    benchmark_metrics: Optional[dict] = None,
    use_llm: bool = True,
    reviewer_timeout_s: float = 60.0,
) -> EvaluationResult:
    """Run the full evaluation pipeline for a mission workspace.

    Args:
        workspace: Path to the mission workspace directory.
        mission_id / mission_name: Metadata for the result.
        run_id: Links back to the benchmark run that produced this workspace.
        mission_passed: Whether mission.check() returned True.
        benchmark_metrics: Optional dict with keys from MissionMetrics.to_dict()
                           — used to enrich reviewer scores with live data.
        use_llm: If True, attempt LLM-based reviewer calls (fallback to static).
        reviewer_timeout_s: Per-reviewer timeout when using LLM.

    Returns:
        Fully populated EvaluationResult.
    """
    t_start = time.monotonic()
    bm = benchmark_metrics or {}

    result = EvaluationResult(
        mission_id=mission_id,
        mission_name=mission_name,
        workspace=workspace,
        run_id=run_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # ── 1. Static quality rules ────────────────────────────────────────────────
    viols, rewards, quality_score = analyze_workspace(workspace)
    result.violations = viols
    result.rewards = rewards

    # ── 2. File list ───────────────────────────────────────────────────────────
    ws = Path(workspace)
    result.files_evaluated = [
        str(p.relative_to(ws))
        for p in ws.rglob("*")
        if p.is_file() and not any(
            part.startswith(".") or part in ("__pycache__", "node_modules")
            for part in p.parts
        )
    ][:100]

    # ── 3. Reviewer agents (parallel) ─────────────────────────────────────────
    par_pct = float(
        (bm.get("parallel") or {}).get("parallel_efficiency_pct", 0)
    )
    max_batch = int(
        (bm.get("parallel") or {}).get("max_batch_size", 0)
    )
    review_scores = run_all_reviewers(
        workspace,
        timeout_s=reviewer_timeout_s,
        use_llm=use_llm,
        benchmark_parallel_pct=par_pct,
        benchmark_max_batch=max_batch,
    )
    result.review_scores = review_scores

    # ── 4. Compute dimension scores ────────────────────────────────────────────
    s = EvaluationScores()

    # Correctness: mission check + build + test outcomes
    s.build_success = 100 if mission_passed else 40
    s.test_success = int(
        (bm.get("commands_run") or 0) > 0
    ) * 60 + 20
    s.correctness = _score_correctness(mission_passed, s.build_success >= 70, s.test_success >= 50)

    # Code quality: static rules primary, LLM overrides if available
    s.code_quality = _merge_llm_static(quality_score, review_scores, "code_quality")

    # Architecture
    arch_rs = review_scores.get("architecture")
    s.architecture = arch_rs.score if arch_rs else 60

    # Testing
    test_rs = review_scores.get("testing")
    s.testing = test_rs.score if test_rs else 40

    # Performance
    perf_rs = review_scores.get("performance")
    s.performance = perf_rs.score if perf_rs else 60

    # Security: reviewer + static rules
    sec_static = security_score_from_violations(viols)
    sec_rs = review_scores.get("security")
    s.security = _merge_llm_static(sec_static, review_scores, "security") if not sec_rs else sec_rs.score

    # Maintainability
    maint_rs = review_scores.get("maintainability")
    s.maintainability = maint_rs.score if maint_rs else 60

    # Documentation
    doc_rs = review_scores.get("documentation")
    s.documentation = doc_rs.score if doc_rs else 50

    # Parallel efficiency — from benchmark data or reviewer
    par_rs = review_scores.get("parallel_efficiency")
    if par_pct > 0:
        s.parallel_efficiency = _clamp(par_pct)
    elif par_rs:
        s.parallel_efficiency = par_rs.score
    else:
        s.parallel_efficiency = 30

    # Memory reuse
    mem = bm.get("memory") or {}
    s.memory_reuse = _score_memory_reuse(
        int(mem.get("speculative_preload_files", 0)),
        int(mem.get("worker_pool_preload_files", 0)),
        int(mem.get("experience_hits", 0)),
    )

    # Recovery
    rec = bm.get("recovery") or {}
    s.recovery_quality = _score_recovery(
        int(rec.get("failures_injected", 0)),
        int(rec.get("auto_recoveries", 0)),
        int(rec.get("human_interventions", 0)),
    )

    s.compute_overall()
    result.scores = s

    result.evaluation_duration_s = time.monotonic() - t_start
    return result


def evaluate_benchmark_run(
    benchmark_run_dict: dict,
    workspaces: dict[str, str],
    use_llm: bool = True,
    reviewer_timeout_s: float = 60.0,
) -> list[EvaluationResult]:
    """Evaluate all missions from a BenchmarkRun dict.

    Args:
        benchmark_run_dict: Output of BenchmarkRun.to_dict().
        workspaces: Maps mission_id → workspace path (if workspaces were kept).
        use_llm: Whether to use LLM reviewers.
        reviewer_timeout_s: Per-reviewer timeout.
    """
    results = []
    run_id = benchmark_run_dict.get("run_id", "")

    for m_dict in benchmark_run_dict.get("missions", []):
        mid = m_dict.get("mission_id", "")
        ws = workspaces.get(mid)
        if not ws:
            continue
        result = evaluate_mission(
            workspace=ws,
            mission_id=mid,
            mission_name=m_dict.get("mission_name", ""),
            run_id=run_id,
            mission_passed=m_dict.get("success", False),
            benchmark_metrics=m_dict,
            use_llm=use_llm,
            reviewer_timeout_s=reviewer_timeout_s,
        )
        results.append(result)

    return results
