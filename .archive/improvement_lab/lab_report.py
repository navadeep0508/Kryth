"""Generates all lab output files for a completed experiment session.

Output files written to lab_history/{exp_id}/:
  experiment_report.md      — full human-readable report
  benchmark_diff.json       — before/after benchmark metrics
  evaluation_diff.json      — before/after evaluation metrics (if available)
  optimization_result.json  — source optimization recommendation
  merge_recommendation.md   — verdict + reasoning for the user
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from improvement_lab.lab_manager import LabSession

logger = logging.getLogger("improvement_lab.lab_report")


class LabReport:
    """Writes all output files for a completed LabSession."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.lab_history = self.project_root / "lab_history"

    def generate_all(self, session: "LabSession") -> dict[str, str]:
        """Write all 5 output files. Returns {name: absolute_path}."""
        out_dir = self.lab_history / session.experiment.id
        out_dir.mkdir(parents=True, exist_ok=True)

        paths: dict[str, str] = {}

        paths["experiment_report.md"] = self._write(
            out_dir / "experiment_report.md",
            self._experiment_report(session),
        )
        paths["benchmark_diff.json"] = self._write(
            out_dir / "benchmark_diff.json",
            self._benchmark_diff_json(session),
        )
        paths["evaluation_diff.json"] = self._write(
            out_dir / "evaluation_diff.json",
            self._evaluation_diff_json(session),
        )
        paths["optimization_result.json"] = self._write(
            out_dir / "optimization_result.json",
            self._optimization_result_json(session),
        )
        paths["merge_recommendation.md"] = self._write(
            out_dir / "merge_recommendation.md",
            self._merge_recommendation_md(session),
        )

        logger.info(f"Lab report written to {out_dir}")
        return paths

    # ── experiment_report.md ─────────────────────────────────────────────────

    def _experiment_report(self, s: "LabSession") -> str:
        cmp = s.comparison
        exp = s.experiment
        rec = s.recommendation
        status_icon = {"completed": "✓", "failed": "✗", "rolled_back": "↩"}.get(s.status, "?")

        lines = [
            f"# KRYTH Autonomous Improvement Laboratory",
            f"",
            f"**Experiment:** `{exp.id}`  "
            f"**Status:** {status_icon} {s.status.upper()}  "
            f"**Started:** {s.started_at[:19]}",
            f"",
            "---",
            "",
            "## Experiment",
            f"",
            f"| Field        | Value |",
            f"|--------------|-------|",
            f"| Type         | `{exp.type}` |",
            f"| Category     | `{exp.category}` |",
            f"| Description  | {exp.description} |",
            f"| Expected gain | ~{exp.expected_gain_pct:.0f}% |",
            f"| Confidence   | {exp.confidence:.2f} |",
            f"",
            f"**Hypothesis:** {exp.hypothesis}",
            f"",
            f"**Source recommendation:** {exp.source_recommendation}",
            f"",
            "### Changes applied to sandbox",
            f"",
        ]
        for i, ch in enumerate(exp.changes, 1):
            lines += [
                f"{i}. **`{ch.relative_path}`** — {ch.description}",
                f"   ```diff",
                f"   - {ch.old_fragment[:120].strip()}",
                f"   + {ch.new_fragment[:120].strip()}",
                f"   ```",
                f"",
            ]

        if cmp:
            lines += [
                "---",
                "",
                "## Results",
                f"",
                f"| Metric                  | Baseline | Experimental | Delta |",
                f"|-------------------------|----------|--------------|-------|",
                f"| Mission success         | {cmp.baseline_pass_rate:.1f}% | {cmp.experimental_pass_rate:.1f}% | {cmp.pass_rate_delta_pct:+.1f}% |",
                f"| Avg duration            | {cmp.baseline_avg_duration_ms:.0f}ms | {cmp.experimental_avg_duration_ms:.0f}ms | {cmp.avg_duration_delta_ms:+.0f}ms |",
                f"| Parallel efficiency     | {cmp.baseline_parallel_efficiency_pct:.1f}% | {cmp.experimental_parallel_efficiency_pct:.1f}% | {cmp.parallel_efficiency_delta_pct:+.1f}% |",
                f"| Total tokens            | {cmp.baseline_total_tokens:,} | {cmp.experimental_total_tokens:,} | {cmp.total_tokens_delta:+,} |",
            ]
            if cmp.eval_available:
                lines.append(
                    f"| Evaluation pass rate    | {cmp.baseline_eval_pass_rate:.1f}% | {cmp.experimental_eval_pass_rate:.1f}% | {cmp.eval_pass_rate_delta_pct:+.1f}% |"
                )
            lines.append("")

            if cmp.improvements:
                lines.append("**Improvements:**")
                for m in cmp.improvements:
                    lines.append(f"- `{m.name}`: {m.baseline:.1f}{m.unit} → {m.experimental:.1f}{m.unit} ({m.delta_pct:+.1f}%)")
                lines.append("")
            if cmp.regressions:
                lines.append("**Regressions:**")
                for m in cmp.regressions:
                    lines.append(f"- `{m.name}`: {m.baseline:.1f}{m.unit} → {m.experimental:.1f}{m.unit} ({m.delta_pct:+.1f}%)")
                lines.append("")

        if rec:
            lines += [
                "---",
                "",
                "## Policy Checks",
                f"",
            ]
            for pc in rec.policy_checks:
                icon = "✓" if pc.passed else "✗"
                lines.append(f"- {icon} **{pc.name}**: {pc.reason}")
            lines.append("")

        if s.error:
            lines += [
                "---",
                "",
                "## Error",
                f"",
                f"```",
                s.error[:2000],
                f"```",
                "",
            ]

        lines += [
            "---",
            "",
            "*Generated by KRYTH Autonomous Improvement Laboratory.*",
            "*This report is advisory only — no code was modified in production.*",
            "",
        ]
        return "\n".join(lines)

    # ── benchmark_diff.json ───────────────────────────────────────────────────

    def _benchmark_diff_json(self, s: "LabSession") -> str:
        cmp = s.comparison
        data: dict = {"experiment_id": s.experiment.id, "status": s.status}
        if cmp:
            data.update({
                "baseline": {
                    "pass_rate_pct": cmp.baseline_pass_rate,
                    "avg_duration_ms": cmp.baseline_avg_duration_ms,
                    "parallel_efficiency_pct": cmp.baseline_parallel_efficiency_pct,
                    "total_tokens": cmp.baseline_total_tokens,
                },
                "experimental": {
                    "pass_rate_pct": cmp.experimental_pass_rate,
                    "avg_duration_ms": cmp.experimental_avg_duration_ms,
                    "parallel_efficiency_pct": cmp.experimental_parallel_efficiency_pct,
                    "total_tokens": cmp.experimental_total_tokens,
                },
                "deltas": {
                    "pass_rate_pct": cmp.pass_rate_delta_pct,
                    "avg_duration_ms": cmp.avg_duration_delta_ms,
                    "avg_duration_pct": cmp.avg_duration_delta_pct,
                    "parallel_efficiency_pct": cmp.parallel_efficiency_delta_pct,
                    "total_tokens": cmp.total_tokens_delta,
                },
                "overall_improved": cmp.overall_improved,
                "overall_regressed": cmp.overall_regressed,
            })
        return json.dumps(data, indent=2)

    # ── evaluation_diff.json ──────────────────────────────────────────────────

    def _evaluation_diff_json(self, s: "LabSession") -> str:
        cmp = s.comparison
        data: dict = {
            "experiment_id": s.experiment.id,
            "available": bool(cmp and cmp.eval_available),
        }
        if cmp and cmp.eval_available:
            data.update({
                "baseline_pass_rate_pct": cmp.baseline_eval_pass_rate,
                "experimental_pass_rate_pct": cmp.experimental_eval_pass_rate,
                "delta_pct": cmp.eval_pass_rate_delta_pct,
            })
        return json.dumps(data, indent=2)

    # ── optimization_result.json ──────────────────────────────────────────────

    def _optimization_result_json(self, s: "LabSession") -> str:
        exp = s.experiment
        data = {
            "experiment_id": exp.id,
            "source_recommendation": exp.source_recommendation,
            "category": exp.category,
            "type": exp.type,
            "hypothesis": exp.hypothesis,
            "expected_gain_pct": exp.expected_gain_pct,
            "confidence": exp.confidence,
            "changes": [
                {
                    "relative_path": c.relative_path,
                    "description": c.description,
                }
                for c in exp.changes
            ],
        }
        return json.dumps(data, indent=2)

    # ── merge_recommendation.md ───────────────────────────────────────────────

    def _merge_recommendation_md(self, s: "LabSession") -> str:
        rec = s.recommendation
        if rec is None:
            return (
                f"# Merge Recommendation\n\n"
                f"Experiment `{s.experiment.id}` did not produce a recommendation "
                f"(status: {s.status}).\n"
            )

        verdict_icon = "✓" if rec.recommend_merge else "✗"
        lines = [
            f"# Merge Recommendation",
            f"",
            f"**Experiment:** `{rec.experiment_id}`",
            f"**Verdict:** {verdict_icon} {'RECOMMEND MERGE' if rec.recommend_merge else 'REJECT'}",
            f"**Confidence:** {rec.confidence:.2f}",
            f"",
            "---",
            "",
            f"## Headline",
            f"",
            f"> {rec.headline}",
            f"",
            f"## Reasoning",
            f"",
            f"```",
            rec.reasoning,
            f"```",
            f"",
        ]
        if rec.risks:
            lines += [
                f"## Risks",
                f"",
            ]
            for risk in rec.risks:
                lines.append(f"- {risk}")
            lines.append("")

        lines += [
            "---",
            "",
            "## To merge (human action required)",
            f"",
            f"```bash",
            f"# Verify the experiment branch",
            f"git log kryth-lab/{rec.experiment_id} --oneline",
            f"",
            f"# Review the diff",
            f"git diff main..kryth-lab/{rec.experiment_id}",
            f"",
            f"# Merge if satisfied",
            f"git checkout main",
            f"git merge kryth-lab/{rec.experiment_id}",
            f"```",
            f"",
            f"**KRYTH will never execute the above commands automatically.**",
            f"",
        ]
        return "\n".join(lines)

    # ── Utility ───────────────────────────────────────────────────────────────

    def _write(self, path: Path, content: str) -> str:
        path.write_text(content, encoding="utf-8")
        return str(path)
