"""Compare two evaluation runs to detect regressions and improvements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .evaluation_metrics import EvaluationRun, EvaluationResult, EvaluationScores


_REGRESSION_THRESHOLD = -10   # points drop = regression
_IMPROVEMENT_THRESHOLD = 10   # points gain = improvement


# ── Per-mission dimension diff ────────────────────────────────────────────────

@dataclass
class DimensionDelta:
    dimension: str
    old_score: int
    new_score: int
    delta: int           # new - old (positive = improved)
    is_regression: bool = False
    is_improvement: bool = False


@dataclass
class MissionEvalDiff:
    mission_id: str
    mission_name: str
    old_overall: int
    new_overall: int
    overall_delta: int
    old_grade: str
    new_grade: str
    dimension_deltas: list[DimensionDelta] = field(default_factory=list)
    is_regression: bool = False
    is_improvement: bool = False
    new_violations: list[str] = field(default_factory=list)   # violations in new not in old
    fixed_violations: list[str] = field(default_factory=list) # violations in old not in new


def diff_evaluations(old: EvaluationResult, new: EvaluationResult) -> MissionEvalDiff:
    d = MissionEvalDiff(
        mission_id=new.mission_id,
        mission_name=new.mission_name,
        old_overall=old.scores.overall,
        new_overall=new.scores.overall,
        overall_delta=new.scores.overall - old.scores.overall,
        old_grade=old.scores.grade(),
        new_grade=new.scores.grade(),
    )

    _DIMS = [
        "correctness", "code_quality", "architecture", "testing",
        "performance", "security", "maintainability", "documentation",
        "parallel_efficiency", "memory_reuse", "recovery_quality",
    ]
    for dim in _DIMS:
        old_s = getattr(old.scores, dim, 0)
        new_s = getattr(new.scores, dim, 0)
        delta = new_s - old_s
        dd = DimensionDelta(
            dimension=dim,
            old_score=old_s,
            new_score=new_s,
            delta=delta,
            is_regression=delta <= _REGRESSION_THRESHOLD,
            is_improvement=delta >= _IMPROVEMENT_THRESHOLD,
        )
        d.dimension_deltas.append(dd)

    d.is_regression = d.overall_delta <= _REGRESSION_THRESHOLD
    d.is_improvement = d.overall_delta >= _IMPROVEMENT_THRESHOLD

    # Violation diff (by message)
    old_msgs = {v.message for v in old.violations}
    new_msgs = {v.message for v in new.violations}
    d.new_violations = sorted(new_msgs - old_msgs)[:10]
    d.fixed_violations = sorted(old_msgs - new_msgs)[:10]

    return d


# ── Run-level comparison ──────────────────────────────────────────────────────

@dataclass
class EvalRunComparison:
    old_run_id: str
    new_run_id: str
    old_timestamp: str
    new_timestamp: str
    old_avg_overall: float
    new_avg_overall: float
    avg_overall_delta: float
    avg_dimension_deltas: dict[str, float] = field(default_factory=dict)
    regressions: list[MissionEvalDiff] = field(default_factory=list)
    improvements: list[MissionEvalDiff] = field(default_factory=list)
    neutral: list[MissionEvalDiff] = field(default_factory=list)
    all_diffs: dict[str, MissionEvalDiff] = field(default_factory=dict)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)

    @property
    def dimension_trend(self) -> dict[str, str]:
        """Returns UP/DOWN/FLAT per dimension."""
        trend = {}
        for dim, delta in self.avg_dimension_deltas.items():
            if delta >= _IMPROVEMENT_THRESHOLD:
                trend[dim] = "UP"
            elif delta <= _REGRESSION_THRESHOLD:
                trend[dim] = "DOWN"
            else:
                trend[dim] = "FLAT"
        return trend

    def summary_lines(self) -> list[str]:
        lines = [
            f"Evaluation Comparison: {self.old_run_id} → {self.new_run_id}",
            f"  Overall score: {self.old_avg_overall:.1f} → {self.new_avg_overall:.1f} "
            f"({self.avg_overall_delta:+.1f})",
            f"",
            "  Dimension trends:",
        ]
        for dim, trend in self.dimension_trend.items():
            delta = self.avg_dimension_deltas.get(dim, 0)
            lines.append(f"    {dim:<22} {trend:<5} ({delta:+.1f})")

        if self.regressions:
            lines.append(f"")
            lines.append(f"  ⚠ Regressions ({len(self.regressions)}):")
            for d in self.regressions:
                lines.append(
                    f"    {d.mission_id} {d.mission_name}: "
                    f"{d.old_overall}→{d.new_overall} ({d.overall_delta:+d})  "
                    f"{d.old_grade}→{d.new_grade}"
                )
        if self.improvements:
            lines.append(f"")
            lines.append(f"  ✓ Improvements ({len(self.improvements)}):")
            for d in self.improvements:
                lines.append(
                    f"    {d.mission_id} {d.mission_name}: "
                    f"{d.old_overall}→{d.new_overall} ({d.overall_delta:+d})  "
                    f"{d.old_grade}→{d.new_grade}"
                )
        return lines


def compare_evaluation_runs(old: EvaluationRun, new: EvaluationRun) -> EvalRunComparison:
    cmp = EvalRunComparison(
        old_run_id=old.run_id,
        new_run_id=new.run_id,
        old_timestamp=old.timestamp,
        new_timestamp=new.timestamp,
        old_avg_overall=old.avg_overall,
        new_avg_overall=new.avg_overall,
        avg_overall_delta=new.avg_overall - old.avg_overall,
    )

    old_by_id = {r.mission_id: r for r in old.results}
    dim_delta_sums: dict[str, list[float]] = {}

    for r_new in new.results:
        r_old = old_by_id.get(r_new.mission_id)
        if r_old is None:
            continue
        d = diff_evaluations(r_old, r_new)
        cmp.all_diffs[d.mission_id] = d
        if d.is_regression:
            cmp.regressions.append(d)
        elif d.is_improvement:
            cmp.improvements.append(d)
        else:
            cmp.neutral.append(d)
        for dd in d.dimension_deltas:
            dim_delta_sums.setdefault(dd.dimension, []).append(dd.delta)

    cmp.avg_dimension_deltas = {
        dim: sum(vals) / len(vals)
        for dim, vals in dim_delta_sums.items()
    }
    return cmp
