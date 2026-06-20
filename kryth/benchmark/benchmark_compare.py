"""Compare two benchmark runs and detect regressions / improvements."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .benchmark_metrics import BenchmarkRun, MissionMetrics


# ── Delta helpers ─────────────────────────────────────────────────────────────

def _pct_change(old: float, new: float) -> float:
    """Return signed % change from old to new (positive = improvement if lower is better)."""
    if old == 0:
        return 0.0
    return (old - new) / old * 100.0


def _ms_delta(old: float, new: float) -> float:
    return new - old


# ── Per-mission diff ──────────────────────────────────────────────────────────

@dataclass
class MissionDiff:
    mission_id: str
    mission_name: str
    # outcome
    old_success: bool
    new_success: bool
    # timing deltas (ms, negative = faster)
    duration_delta_ms: float = 0.0
    first_tool_call_delta_ms: float = 0.0
    first_write_delta_ms: float = 0.0
    first_chunk_delta_ms: float = 0.0
    # token deltas
    tokens_in_delta: int = 0
    tokens_out_delta: int = 0
    # parallel
    parallel_eff_delta_pct: float = 0.0
    # flags
    is_regression: bool = False       # was pass → now fail, or >20% slower
    is_improvement: bool = False      # was fail → now pass, or >20% faster


def diff_missions(old: MissionMetrics, new: MissionMetrics) -> MissionDiff:
    d = MissionDiff(
        mission_id=new.mission_id,
        mission_name=new.mission_name,
        old_success=old.success,
        new_success=new.success,
    )
    d.duration_delta_ms = _ms_delta(old.timings.duration_ms, new.timings.duration_ms)
    d.first_tool_call_delta_ms = _ms_delta(
        old.timings.first_tool_call_ms, new.timings.first_tool_call_ms
    )
    d.first_write_delta_ms = _ms_delta(
        old.timings.first_write_ms, new.timings.first_write_ms
    )
    d.first_chunk_delta_ms = _ms_delta(
        old.timings.first_stream_begin_ms, new.timings.first_stream_begin_ms
    )
    d.tokens_in_delta = new.tokens_in - old.tokens_in
    d.tokens_out_delta = new.tokens_out - old.tokens_out
    d.parallel_eff_delta_pct = (
        new.parallel.parallel_efficiency_pct - old.parallel.parallel_efficiency_pct
    )

    # Regression: pass → fail, or duration grew by >20%
    pct = _pct_change(old.timings.duration_ms, new.timings.duration_ms)
    if (old.success and not new.success) or pct < -20:  # pct negative = got slower
        d.is_regression = True
    # Improvement: fail → pass, or duration shrank by >20%
    if (not old.success and new.success) or pct > 20:
        d.is_improvement = True

    return d


# ── Run-level comparison ──────────────────────────────────────────────────────

@dataclass
class RunComparison:
    old_run_id: str
    new_run_id: str
    old_timestamp: str
    new_timestamp: str
    # aggregate changes
    pass_rate_delta_pct: float = 0.0
    avg_duration_delta_ms: float = 0.0
    total_tokens_delta: int = 0
    # regressions / improvements
    regressions: list[MissionDiff] = field(default_factory=list)
    improvements: list[MissionDiff] = field(default_factory=list)
    neutral: list[MissionDiff] = field(default_factory=list)
    # all diffs keyed by mission_id
    diffs: dict[str, MissionDiff] = field(default_factory=dict)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)

    def summary_lines(self) -> list[str]:
        lines = [
            f"Comparison: {self.old_run_id} → {self.new_run_id}",
            f"  Pass rate: {self.pass_rate_delta_pct:+.1f}%",
            f"  Avg duration: {self.avg_duration_delta_ms:+.0f}ms",
            f"  Total tokens: {self.total_tokens_delta:+d}",
        ]
        if self.regressions:
            lines.append(f"  Regressions ({len(self.regressions)}):")
            for d in self.regressions:
                lines.append(
                    f"    {d.mission_id} {d.mission_name}: "
                    f"{d.duration_delta_ms:+.0f}ms  "
                    f"{'PASS→FAIL' if d.old_success and not d.new_success else ''}"
                )
        if self.improvements:
            lines.append(f"  Improvements ({len(self.improvements)}):")
            for d in self.improvements:
                lines.append(
                    f"    {d.mission_id} {d.mission_name}: "
                    f"{d.duration_delta_ms:+.0f}ms  "
                    f"{'FAIL→PASS' if not d.old_success and d.new_success else ''}"
                )
        return lines


def compare_runs(old: BenchmarkRun, new: BenchmarkRun) -> RunComparison:
    cmp = RunComparison(
        old_run_id=old.run_id,
        new_run_id=new.run_id,
        old_timestamp=old.timestamp,
        new_timestamp=new.timestamp,
    )
    cmp.pass_rate_delta_pct = new.pass_rate_pct - old.pass_rate_pct
    cmp.avg_duration_delta_ms = new.avg_duration_ms - old.avg_duration_ms
    cmp.total_tokens_delta = new.total_tokens - old.total_tokens

    old_by_id = {m.mission_id: m for m in old.missions}
    for m_new in new.missions:
        mid = m_new.mission_id
        m_old = old_by_id.get(mid)
        if m_old is None:
            continue
        d = diff_missions(m_old, m_new)
        cmp.diffs[mid] = d
        if d.is_regression:
            cmp.regressions.append(d)
        elif d.is_improvement:
            cmp.improvements.append(d)
        else:
            cmp.neutral.append(d)

    return cmp
