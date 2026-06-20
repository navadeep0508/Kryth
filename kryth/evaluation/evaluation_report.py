"""Generates Markdown and text reports from evaluation data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .evaluation_metrics import EvaluationResult, EvaluationRun, EvaluationScores
from .evaluation_compare import EvalRunComparison


# ── Formatting helpers ────────────────────────────────────────────────────────

def _grade_bar(score: int) -> str:
    filled = score // 5  # 20 chars wide
    return "█" * filled + "░" * (20 - filled)


def _grade_emoji(score: int) -> str:
    if score >= 90: return "🏆"
    if score >= 80: return "✅"
    if score >= 70: return "🟡"
    if score >= 60: return "🟠"
    return "🔴"


def _dim_row(label: str, score: int) -> str:
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    return f"| {label:<22} | [{bar}] {score:3d}/100 |"


# ── Per-mission section ───────────────────────────────────────────────────────

def _mission_section(r: EvaluationResult) -> list[str]:
    s = r.scores
    lines = [
        f"### [{r.mission_id}] {r.mission_name}",
        f"",
        f"**Overall: {s.overall}/100** {_grade_emoji(s.overall)} (Grade {s.grade()})",
        f"",
        f"| Dimension             | Score               |",
        f"|----------------------|---------------------|",
        _dim_row("Correctness",       s.correctness),
        _dim_row("Code Quality",      s.code_quality),
        _dim_row("Architecture",      s.architecture),
        _dim_row("Testing",           s.testing),
        _dim_row("Performance",       s.performance),
        _dim_row("Security",          s.security),
        _dim_row("Maintainability",   s.maintainability),
        _dim_row("Documentation",     s.documentation),
        _dim_row("Parallel Eff.",     s.parallel_efficiency),
        _dim_row("Memory Reuse",      s.memory_reuse),
        _dim_row("Recovery Quality",  s.recovery_quality),
        f"",
    ]

    # Reviewer findings
    if r.review_scores:
        lines.append("**Reviewer Findings:**")
        lines.append("")
        for dim, rs in r.review_scores.items():
            if rs.findings:
                lines.append(f"*{dim.replace('_', ' ').title()}* ({rs.reviewer}, {rs.score}/100):")
                for finding in rs.findings[:5]:
                    lines.append(f"  - {finding}")
        lines.append("")

    # Violations summary
    errors = [v for v in r.violations if v.severity == "error"]
    warnings = [v for v in r.violations if v.severity == "warning"]
    if errors or warnings:
        lines.append(f"**Issues:** {len(errors)} errors, {len(warnings)} warnings")
        for v in errors[:5]:
            lines.append(f"  - ERROR [{v.rule_id}] `{Path(v.file).name}:{v.line}` — {v.message}")
        for v in warnings[:5]:
            lines.append(f"  - WARN  [{v.rule_id}] `{Path(v.file).name}:{v.line}` — {v.message}")
        lines.append("")

    # Rewards summary
    if r.rewards:
        lines.append(f"**Rewards:** (+{sum(rw.bonus for rw in r.rewards)} pts)")
        for rw in r.rewards[:5]:
            lines.append(f"  - {rw.message}")
        lines.append("")

    lines.append(f"*Evaluation duration: {r.evaluation_duration_s:.1f}s*")
    lines.append("")
    return lines


# ── Summary table ─────────────────────────────────────────────────────────────

def _summary_table(run: EvaluationRun) -> list[str]:
    header = (
        f"| ID  | Mission                        | Overall | Grade "
        f"| Correct | Quality | Security | Testing |"
    )
    sep = (
        f"|-----|--------------------------------|---------|-------|"
        f"---------|---------|----------|---------|"
    )
    lines = [header, sep]
    for r in run.results:
        s = r.scores
        lines.append(
            f"| {r.mission_id:<3} | {r.mission_name[:30]:<30} "
            f"| {s.overall:>7} | {s.grade():<5} "
            f"| {s.correctness:>7} | {s.code_quality:>7} "
            f"| {s.security:>8} | {s.testing:>7} |"
        )
    return lines


# ── Comparison section ────────────────────────────────────────────────────────

def _comparison_section(cmp: EvalRunComparison) -> list[str]:
    lines = [
        f"## Comparison vs Previous Evaluation",
        f"",
        f"**Previous:** `{cmp.old_run_id}` ({cmp.old_timestamp[:19]})",
        f"**Current:**  `{cmp.new_run_id}` ({cmp.new_timestamp[:19]})",
        f"",
        f"| Metric         | Old   | New   | Delta  |",
        f"|----------------|-------|-------|--------|",
        f"| Avg Overall    | {cmp.old_avg_overall:.1f} | {cmp.new_avg_overall:.1f} | {cmp.avg_overall_delta:+.1f} |",
        f"",
        f"**Dimension Trends:**",
        f"",
    ]
    trend_map = cmp.dimension_trend
    for dim, trend in trend_map.items():
        delta = cmp.avg_dimension_deltas.get(dim, 0)
        arrow = "↑" if trend == "UP" else ("↓" if trend == "DOWN" else "→")
        lines.append(f"- {arrow} **{dim.replace('_', ' ').title()}**: {delta:+.1f}")

    if cmp.regressions:
        lines += [f"", f"### ⚠ Regressions"]
        for d in cmp.regressions:
            lines.append(
                f"- **{d.mission_id}** {d.mission_name}: "
                f"{d.old_overall}→{d.new_overall} ({d.overall_delta:+d})  "
                f"Grade {d.old_grade}→{d.new_grade}"
            )
            for dd in d.dimension_deltas:
                if dd.is_regression:
                    lines.append(f"  - {dd.dimension}: {dd.old_score}→{dd.new_score} ({dd.delta:+d})")

    if cmp.improvements:
        lines += [f"", f"### ✓ Improvements"]
        for d in cmp.improvements:
            lines.append(
                f"- **{d.mission_id}** {d.mission_name}: "
                f"{d.old_overall}→{d.new_overall} ({d.overall_delta:+d})  "
                f"Grade {d.old_grade}→{d.new_grade}"
            )

    lines.append("")
    return lines


# ── Engineering trend section ─────────────────────────────────────────────────

def _engineering_trend_section(run: EvaluationRun) -> list[str]:
    """Summarizes what kind of engineer KRYTH is based on dimension averages."""
    avgs = run.avg_by_dimension

    strengths = [k for k, v in avgs.items() if v >= 75]
    weaknesses = [k for k, v in avgs.items() if v < 55]

    lines = [
        f"## Engineering Profile",
        f"",
        f"Average dimension scores across all {run.total} missions:",
        f"",
    ]
    for dim, avg in sorted(avgs.items(), key=lambda x: -x[1]):
        bar = "█" * int(avg // 10) + "░" * (10 - int(avg // 10))
        lines.append(f"- **{dim.replace('_', ' ').title():<24}** [{bar}] {avg:.0f}")

    lines.append("")
    if strengths:
        lines.append(f"**Strengths:** {', '.join(k.replace('_', ' ') for k in strengths)}")
    if weaknesses:
        lines.append(f"**Needs Improvement:** {', '.join(k.replace('_', ' ') for k in weaknesses)}")
    lines.append("")
    return lines


# ── Top-level report generators ───────────────────────────────────────────────

def generate_evaluation_markdown(
    run: EvaluationRun,
    comparison: Optional[EvalRunComparison] = None,
    output_path: Optional[str] = None,
) -> str:
    lines = [
        f"# KRYTH Autonomous Evaluation Report",
        f"",
        f"**Evaluation Run:** `{run.run_id}`  ",
        f"**Timestamp:** {run.timestamp[:19]}  ",
        f"**KRYTH Version:** {run.kryth_version}  ",
        f"**Missions Evaluated:** {run.total}  ",
        f"",
        f"## Overall Score: {run.avg_overall:.0f}/100",
        f"",
        f"[{_grade_bar(int(run.avg_overall))}] {run.avg_overall:.1f}/100",
        f"",
        f"## Summary Table",
        f"",
    ]
    lines += _summary_table(run)
    lines.append("")

    lines += _engineering_trend_section(run)

    if comparison:
        lines += _comparison_section(comparison)

    lines += [
        f"## Per-Mission Evaluation Details",
        f"",
    ]
    for r in run.results:
        lines += _mission_section(r)

    report = "\n".join(lines)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report, encoding="utf-8")
    return report


def generate_evaluation_text_summary(run: EvaluationRun) -> str:
    lines = [
        f"",
        f"{'═'*65}",
        f"  KRYTH EVALUATION — {run.run_id}",
        f"  Average Overall Score: {run.avg_overall:.0f}/100",
        f"{'═'*65}",
    ]
    for r in run.results:
        s = r.scores
        lines.append(
            f"  [{r.mission_id}] {r.mission_name[:30]:<30}  "
            f"Overall={s.overall:3d}  "
            f"Q={s.code_quality:3d}  "
            f"Sec={s.security:3d}  "
            f"Test={s.testing:3d}  "
            f"{s.grade()}"
        )
    lines.append(f"{'═'*65}")
    lines.append("")
    return "\n".join(lines)


def save_json_evaluation(run: EvaluationRun, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(run.to_dict(), fh, indent=2, default=str)
