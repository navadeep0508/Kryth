"""Applies merge policy to a ComparisonResult and produces a MergeRecommendation.

Merge policy — RECOMMEND only if ALL of:
  ✓ Mission success rate not reduced (pass_rate_delta ≥ -0.5%)
  ✓ Evaluation pass rate not reduced (if eval is available)
  ✓ Parallel efficiency not significantly regressed (delta ≥ -3%)
  ✓ Token usage not significantly increased (delta ≤ +20%)
  ✓ At least one metric improved
  ✓ Confidence (from experiment) meets minimum threshold (≥ 0.40)

REJECT if ANY policy check fails.

The recommender NEVER merges — it only produces a recommendation text
that requires human approval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from improvement_lab.comparison_engine import ComparisonResult
from improvement_lab.experiment_generator import Experiment

logger = logging.getLogger("improvement_lab.merge_recommender")

_MIN_CONFIDENCE = 0.40
_MAX_TOKEN_INCREASE_PCT = 20.0
_MAX_PASS_RATE_REGRESSION_PCT = -0.5
_MAX_PARALLEL_REGRESSION_PCT = -3.0


@dataclass
class PolicyCheck:
    name: str
    passed: bool
    baseline_value: float
    experimental_value: float
    delta: float
    reason: str


@dataclass
class MergeRecommendation:
    """Human-readable recommendation produced by the lab — never auto-applied."""
    recommend_merge: bool
    confidence: float              # calibrated confidence (0–1)
    headline: str                  # one-line verdict
    policy_checks: list[PolicyCheck]
    expected_gain_pct: float
    actual_gain_pct: float         # derived from comparison
    risks: list[str]
    reasoning: str                 # multi-sentence rationale
    experiment_id: str = ""
    experiment_type: str = ""
    experiment_description: str = ""


class MergeRecommender:
    """Applies the merge policy and generates a structured recommendation."""

    def recommend(
        self,
        experiment: Experiment,
        comparison: ComparisonResult,
        calibrated_confidence: Optional[float] = None,
    ) -> MergeRecommendation:
        confidence = calibrated_confidence if calibrated_confidence is not None else experiment.confidence
        checks = self._run_policy(comparison, confidence)
        passed_all = all(c.passed for c in checks)

        actual_gain_pct = self._compute_actual_gain(comparison)
        risks = self._identify_risks(comparison, checks)
        headline = self._build_headline(experiment, comparison, passed_all, actual_gain_pct)
        reasoning = self._build_reasoning(experiment, comparison, checks, actual_gain_pct)

        rec = MergeRecommendation(
            recommend_merge=passed_all,
            confidence=confidence,
            headline=headline,
            policy_checks=checks,
            expected_gain_pct=experiment.expected_gain_pct,
            actual_gain_pct=actual_gain_pct,
            risks=risks,
            reasoning=reasoning,
            experiment_id=experiment.id,
            experiment_type=experiment.type,
            experiment_description=experiment.description,
        )

        verdict = "RECOMMEND MERGE" if passed_all else "REJECT"
        logger.info(
            f"[{experiment.id}] Merge recommendation: {verdict}  "
            f"confidence={confidence:.2f}  actual_gain={actual_gain_pct:+.1f}%"
        )
        return rec

    # ── Policy checks ─────────────────────────────────────────────────────────

    def _run_policy(self, cmp: ComparisonResult, confidence: float) -> list[PolicyCheck]:
        checks = []

        # 1. Mission success not reduced
        checks.append(PolicyCheck(
            name="mission_success_not_reduced",
            passed=cmp.pass_rate_delta_pct >= _MAX_PASS_RATE_REGRESSION_PCT,
            baseline_value=cmp.baseline_pass_rate,
            experimental_value=cmp.experimental_pass_rate,
            delta=cmp.pass_rate_delta_pct,
            reason=(
                "Mission success rate unchanged or improved"
                if cmp.pass_rate_delta_pct >= _MAX_PASS_RATE_REGRESSION_PCT
                else f"Mission success dropped by {abs(cmp.pass_rate_delta_pct):.1f}%"
            ),
        ))

        # 2. Evaluation pass rate not reduced (skip if not available)
        if cmp.eval_available:
            checks.append(PolicyCheck(
                name="evaluation_not_reduced",
                passed=cmp.eval_pass_rate_delta_pct >= _MAX_PASS_RATE_REGRESSION_PCT,
                baseline_value=cmp.baseline_eval_pass_rate,
                experimental_value=cmp.experimental_eval_pass_rate,
                delta=cmp.eval_pass_rate_delta_pct,
                reason=(
                    "Evaluation pass rate unchanged or improved"
                    if cmp.eval_pass_rate_delta_pct >= _MAX_PASS_RATE_REGRESSION_PCT
                    else f"Evaluation dropped by {abs(cmp.eval_pass_rate_delta_pct):.1f}%"
                ),
            ))

        # 3. Parallel efficiency not significantly regressed
        checks.append(PolicyCheck(
            name="parallel_efficiency_not_regressed",
            passed=cmp.parallel_efficiency_delta_pct >= _MAX_PARALLEL_REGRESSION_PCT,
            baseline_value=cmp.baseline_parallel_efficiency_pct,
            experimental_value=cmp.experimental_parallel_efficiency_pct,
            delta=cmp.parallel_efficiency_delta_pct,
            reason=(
                "Parallel efficiency maintained or improved"
                if cmp.parallel_efficiency_delta_pct >= _MAX_PARALLEL_REGRESSION_PCT
                else f"Parallel efficiency dropped by {abs(cmp.parallel_efficiency_delta_pct):.1f}%"
            ),
        ))

        # 4. Token usage not significantly increased
        token_delta_pct = (
            (cmp.total_tokens_delta / cmp.baseline_total_tokens * 100)
            if cmp.baseline_total_tokens > 0 else 0.0
        )
        checks.append(PolicyCheck(
            name="token_usage_not_inflated",
            passed=token_delta_pct <= _MAX_TOKEN_INCREASE_PCT,
            baseline_value=float(cmp.baseline_total_tokens),
            experimental_value=float(cmp.experimental_total_tokens),
            delta=token_delta_pct,
            reason=(
                f"Token usage acceptable ({token_delta_pct:+.1f}%)"
                if token_delta_pct <= _MAX_TOKEN_INCREASE_PCT
                else f"Token usage inflated by {token_delta_pct:.1f}%"
            ),
        ))

        # 5. At least one metric improved
        any_improved = bool(cmp.improvements)
        checks.append(PolicyCheck(
            name="at_least_one_improvement",
            passed=any_improved,
            baseline_value=0,
            experimental_value=float(len(cmp.improvements)),
            delta=float(len(cmp.improvements)),
            reason=(
                f"{len(cmp.improvements)} metric(s) improved"
                if any_improved
                else "No metrics improved"
            ),
        ))

        # 6. Minimum confidence
        checks.append(PolicyCheck(
            name="confidence_threshold",
            passed=confidence >= _MIN_CONFIDENCE,
            baseline_value=_MIN_CONFIDENCE,
            experimental_value=confidence,
            delta=confidence - _MIN_CONFIDENCE,
            reason=(
                f"Confidence {confidence:.2f} meets threshold {_MIN_CONFIDENCE}"
                if confidence >= _MIN_CONFIDENCE
                else f"Confidence {confidence:.2f} below threshold {_MIN_CONFIDENCE}"
            ),
        ))

        return checks

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_actual_gain(self, cmp: ComparisonResult) -> float:
        """Estimate actual % gain from the duration delta (negative = faster = good)."""
        if cmp.baseline_avg_duration_ms <= 0:
            return 0.0
        return -cmp.avg_duration_delta_pct   # positive = improvement

    def _identify_risks(self, cmp: ComparisonResult, checks: list[PolicyCheck]) -> list[str]:
        risks = []
        if cmp.pass_rate_delta_pct < 0:
            risks.append(f"Mission success regressed {cmp.pass_rate_delta_pct:+.1f}%")
        if cmp.parallel_efficiency_delta_pct < -1.0:
            risks.append(f"Parallel efficiency dropped {cmp.parallel_efficiency_delta_pct:+.1f}%")
        if cmp.total_tokens_delta > 0:
            risks.append(f"Token usage increased by {cmp.total_tokens_delta:+,}")
        failed_checks = [c for c in checks if not c.passed]
        for c in failed_checks:
            risks.append(f"Policy failed: {c.name} — {c.reason}")
        return risks

    def _build_headline(
        self,
        exp: Experiment,
        cmp: ComparisonResult,
        passed: bool,
        actual_gain_pct: float,
    ) -> str:
        verdict = "[RECOMMEND MERGE]" if passed else "[REJECT]"
        return (
            f"{verdict} | {exp.description} | "
            f"pass_rate {cmp.pass_rate_delta_pct:+.1f}% | "
            f"duration {cmp.avg_duration_delta_ms:+.0f}ms ({actual_gain_pct:+.1f}%)"
        )

    def _build_reasoning(
        self,
        exp: Experiment,
        cmp: ComparisonResult,
        checks: list[PolicyCheck],
        actual_gain_pct: float,
    ) -> str:
        lines = [
            f"Experiment: {exp.description}",
            f"Hypothesis: {exp.hypothesis}",
            "",
            f"Observed: mission success {cmp.baseline_pass_rate:.1f}% → "
            f"{cmp.experimental_pass_rate:.1f}% ({cmp.pass_rate_delta_pct:+.1f}%)",
            f"Observed: avg duration {cmp.avg_duration_delta_ms:+.0f}ms "
            f"({actual_gain_pct:+.1f}% improvement)",
            f"Observed: parallel efficiency {cmp.baseline_parallel_efficiency_pct:.1f}% → "
            f"{cmp.experimental_parallel_efficiency_pct:.1f}%",
            "",
            "Policy checks:",
        ]
        for c in checks:
            icon = "✓" if c.passed else "✗"
            lines.append(f"  {icon} {c.name}: {c.reason}")

        passed_all = all(c.passed for c in checks)
        lines += [
            "",
            "RECOMMENDATION: " + (
                f"Merge is safe. Expected gain {exp.expected_gain_pct:.0f}%, "
                f"actual gain {actual_gain_pct:+.1f}%. "
                "Quality and success unchanged. Awaiting human approval."
                if passed_all else
                f"Do not merge. One or more policy checks failed. "
                "Experiment will be rolled back and lesson recorded."
            ),
        ]
        return "\n".join(lines)
