"""Compares baseline vs experimental runs across all key metrics.

Produces a ComparisonResult that the MergeRecommender uses to apply
policy checks (no regressions in success/security/quality).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from improvement_lab.evaluation_runner import EvalResult

logger = logging.getLogger("improvement_lab.comparison_engine")


@dataclass
class MetricDelta:
    name: str
    baseline: float
    experimental: float
    delta: float           # experimental - baseline
    delta_pct: float       # delta / |baseline| * 100  (signed)
    improved: bool
    regressed: bool
    unit: str = ""


@dataclass
class ComparisonResult:
    """Full comparison between baseline and experimental benchmark runs."""

    # Mission success
    baseline_pass_rate: float = 0.0
    experimental_pass_rate: float = 0.0
    pass_rate_delta_pct: float = 0.0

    # Duration
    baseline_avg_duration_ms: float = 0.0
    experimental_avg_duration_ms: float = 0.0
    avg_duration_delta_ms: float = 0.0
    avg_duration_delta_pct: float = 0.0

    # Tokens
    baseline_total_tokens: int = 0
    experimental_total_tokens: int = 0
    total_tokens_delta: int = 0

    # Parallel efficiency
    baseline_parallel_efficiency_pct: float = 0.0
    experimental_parallel_efficiency_pct: float = 0.0
    parallel_efficiency_delta_pct: float = 0.0

    # Evaluation
    baseline_eval_pass_rate: float = 0.0
    experimental_eval_pass_rate: float = 0.0
    eval_pass_rate_delta_pct: float = 0.0
    eval_available: bool = False

    # Per-mission deltas
    metrics: list[MetricDelta] = field(default_factory=list)
    regressions: list[MetricDelta] = field(default_factory=list)
    improvements: list[MetricDelta] = field(default_factory=list)

    # Summary
    overall_improved: bool = False
    overall_regressed: bool = False
    summary: str = ""


class ComparisonEngine:
    """Compares baseline vs experimental BenchmarkRun and EvalResult."""

    def compare(
        self,
        baseline_bench,       # BenchmarkRun
        experimental_bench,   # BenchmarkRun
        baseline_eval: Optional[EvalResult] = None,
        experimental_eval: Optional[EvalResult] = None,
    ) -> ComparisonResult:
        r = ComparisonResult()

        # ── Mission pass rate ─────────────────────────────────────────────────
        r.baseline_pass_rate = self._safe_float(baseline_bench, "pass_rate_pct")
        r.experimental_pass_rate = self._safe_float(experimental_bench, "pass_rate_pct")
        r.pass_rate_delta_pct = r.experimental_pass_rate - r.baseline_pass_rate

        # ── Average duration ──────────────────────────────────────────────────
        r.baseline_avg_duration_ms = self._safe_float(baseline_bench, "avg_duration_ms")
        r.experimental_avg_duration_ms = self._safe_float(experimental_bench, "avg_duration_ms")
        r.avg_duration_delta_ms = r.experimental_avg_duration_ms - r.baseline_avg_duration_ms
        if r.baseline_avg_duration_ms > 0:
            r.avg_duration_delta_pct = (
                r.avg_duration_delta_ms / r.baseline_avg_duration_ms * 100
            )

        # ── Token usage ───────────────────────────────────────────────────────
        r.baseline_total_tokens = int(self._safe_float(baseline_bench, "total_tokens"))
        r.experimental_total_tokens = int(self._safe_float(experimental_bench, "total_tokens"))
        r.total_tokens_delta = r.experimental_total_tokens - r.baseline_total_tokens

        # ── Parallel efficiency ───────────────────────────────────────────────
        r.baseline_parallel_efficiency_pct = self._parallel_eff(baseline_bench)
        r.experimental_parallel_efficiency_pct = self._parallel_eff(experimental_bench)
        r.parallel_efficiency_delta_pct = (
            r.experimental_parallel_efficiency_pct - r.baseline_parallel_efficiency_pct
        )

        # ── Build metric deltas ───────────────────────────────────────────────
        r.metrics = self._build_metrics(r)
        r.regressions = [m for m in r.metrics if m.regressed]
        r.improvements = [m for m in r.metrics if m.improved]

        # ── Evaluation ────────────────────────────────────────────────────────
        if baseline_eval and baseline_eval.available and experimental_eval and experimental_eval.available:
            r.eval_available = True
            r.baseline_eval_pass_rate = baseline_eval.pass_rate_pct
            r.experimental_eval_pass_rate = experimental_eval.pass_rate_pct
            r.eval_pass_rate_delta_pct = (
                r.experimental_eval_pass_rate - r.baseline_eval_pass_rate
            )

        # ── Overall ───────────────────────────────────────────────────────────
        critical_regressed = any(
            m.regressed and m.name in ("pass_rate", "parallel_efficiency")
            for m in r.metrics
        )
        any_improved = any(m.improved for m in r.metrics)

        r.overall_regressed = critical_regressed
        r.overall_improved = any_improved and not critical_regressed
        r.summary = self._build_summary(r)

        logger.info(
            f"Comparison: pass_rate {r.baseline_pass_rate:.1f}% → "
            f"{r.experimental_pass_rate:.1f}%  "
            f"duration {r.avg_duration_delta_ms:+.0f}ms  "
            f"improved={r.overall_improved}  regressed={r.overall_regressed}"
        )
        return r

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _safe_float(self, obj, attr: str) -> float:
        try:
            v = getattr(obj, attr, None)
            if v is None and isinstance(obj, dict):
                v = obj.get(attr)
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def _parallel_eff(self, bench) -> float:
        missions = getattr(bench, "missions", None) or []
        if not missions:
            return 0.0
        vals = [
            getattr(getattr(m, "parallel", None), "parallel_efficiency_pct", 0)
            for m in missions
        ]
        return sum(vals) / len(vals) if vals else 0.0

    def _build_metrics(self, r: ComparisonResult) -> list[MetricDelta]:
        specs = [
            ("pass_rate",           r.baseline_pass_rate,              r.experimental_pass_rate,          "%",  True),
            ("avg_duration_ms",     r.baseline_avg_duration_ms,        r.experimental_avg_duration_ms,    "ms", False),
            ("parallel_efficiency", r.baseline_parallel_efficiency_pct, r.experimental_parallel_efficiency_pct, "%", True),
            ("total_tokens",        float(r.baseline_total_tokens),    float(r.experimental_total_tokens), "",  False),
        ]
        metrics = []
        for name, base, exp, unit, higher_is_better in specs:
            delta = exp - base
            delta_pct = (delta / abs(base) * 100) if base != 0 else 0.0
            if higher_is_better:
                improved = delta > 0.5
                regressed = delta < -0.5
            else:
                improved = delta < -1.0
                regressed = delta > 1.0
            metrics.append(MetricDelta(
                name=name,
                baseline=base,
                experimental=exp,
                delta=delta,
                delta_pct=delta_pct,
                improved=improved,
                regressed=regressed,
                unit=unit,
            ))
        return metrics

    def _build_summary(self, r: ComparisonResult) -> str:
        parts = [
            f"pass_rate {r.baseline_pass_rate:.1f}% → {r.experimental_pass_rate:.1f}% "
            f"({r.pass_rate_delta_pct:+.1f}%)",
            f"avg_duration {r.avg_duration_delta_ms:+.0f}ms "
            f"({r.avg_duration_delta_pct:+.1f}%)",
            f"parallel_eff {r.baseline_parallel_efficiency_pct:.1f}% → "
            f"{r.experimental_parallel_efficiency_pct:.1f}%",
        ]
        return " | ".join(parts)
