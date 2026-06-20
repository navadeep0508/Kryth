"""Bottleneck detector — ranked list of observed performance issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics
from optimization.execution_profiler import ExecutionProfile


SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass
class Bottleneck:
    severity: str           # CRITICAL / HIGH / MEDIUM / LOW
    category: str           # PLANNING / EXPLORATION / PARALLEL / TESTING /
                            # MEMORY / API / WORKER / STREAMING / RECOVERY
    description: str
    mission_ids: List[str] = field(default_factory=list)
    metric_value: float = 0.0
    metric_unit: str = ""

    @property
    def rank(self) -> int:
        return SEVERITY_RANK.get(self.severity, 99)


@dataclass
class BottleneckReport:
    bottlenecks: List[Bottleneck] = field(default_factory=list)

    def sorted_by_severity(self) -> List[Bottleneck]:
        return sorted(self.bottlenecks, key=lambda b: b.rank)


# Thresholds
_SLOW_PLANNING_MS = 15_000       # >15s before first tool
_SLOW_EXPLORATION_MS = 60_000    # >60s reading before writing
_LOW_PARALLEL_PCT = 40.0         # <40% parallel efficiency
_HIGH_READ_RATIO = 5.0           # >5 reads per write
_LOW_TOOLS_PER_TURN = 1.2        # <1.2 tools/turn → many single-tool turns
_HIGH_TOKEN_PER_FILE = 15_000    # >15k tokens per file written


def detect_bottlenecks(run: BenchmarkRun, profile: ExecutionProfile) -> BottleneckReport:
    report = BottleneckReport()
    add = report.bottlenecks.append

    passed = [m for m in run.missions if m.success]
    failed = [m for m in run.missions if not m.success]

    # ── Failures ────────────────────────────────────────────────────────────────
    if failed:
        add(Bottleneck(
            severity="CRITICAL",
            category="FAILURE",
            description=f"{len(failed)} mission(s) failed: {', '.join(m.mission_id for m in failed)}",
            mission_ids=[m.mission_id for m in failed],
            metric_value=len(failed),
            metric_unit="missions",
        ))

    # ── API / retry pressure ─────────────────────────────────────────────────────
    api_error_missions = [m for m in run.missions if "api_error" in (m.error or "").lower()
                          or "retry" in (m.error or "").lower()]
    # Also estimate from missions that timed out or had high duration vs files written
    slow_starts = [
        p for p in profile.missions
        if p.planning_ms > _SLOW_PLANNING_MS and p.success
    ]
    if slow_starts:
        avg_planning = sum(p.planning_ms for p in slow_starts) / len(slow_starts)
        add(Bottleneck(
            severity="HIGH",
            category="PLANNING",
            description=(
                f"{len(slow_starts)} mission(s) waited >{_SLOW_PLANNING_MS/1000:.0f}s before first tool call "
                f"(avg {avg_planning/1000:.1f}s) — likely API rate pressure or slow task classification"
            ),
            mission_ids=[p.mission_id for p in slow_starts],
            metric_value=avg_planning / 1000,
            metric_unit="s",
        ))

    # ── Long exploration phase ───────────────────────────────────────────────────
    slow_explore = [
        p for p in profile.missions
        if p.exploration_ms > _SLOW_EXPLORATION_MS and p.success
    ]
    if slow_explore:
        avg_exp = sum(p.exploration_ms for p in slow_explore) / len(slow_explore)
        add(Bottleneck(
            severity="MEDIUM",
            category="EXPLORATION",
            description=(
                f"{len(slow_explore)} mission(s) spent >{_SLOW_EXPLORATION_MS/1000:.0f}s reading before first write "
                f"(avg {avg_exp/1000:.1f}s) — excessive exploration before implementation"
            ),
            mission_ids=[p.mission_id for p in slow_explore],
            metric_value=avg_exp / 1000,
            metric_unit="s",
        ))

    # ── Low parallel efficiency ──────────────────────────────────────────────────
    low_par = [
        m for m in passed
        if m.parallel.parallel_efficiency_pct < _LOW_PARALLEL_PCT
        and m.total_tool_calls > 2
    ]
    if low_par:
        avg_par = sum(m.parallel.parallel_efficiency_pct for m in low_par) / len(low_par)
        add(Bottleneck(
            severity="MEDIUM",
            category="PARALLEL",
            description=(
                f"{len(low_par)} mission(s) have parallel efficiency below {_LOW_PARALLEL_PCT:.0f}% "
                f"(avg {avg_par:.1f}%) — tool calls executed serially instead of in batches"
            ),
            mission_ids=[m.mission_id for m in low_par],
            metric_value=avg_par,
            metric_unit="%",
        ))

    # ── No tests ────────────────────────────────────────────────────────────────
    no_tests = [m for m in passed if m.timings.first_test_ms < 0]
    if no_tests:
        add(Bottleneck(
            severity="MEDIUM",
            category="TESTING",
            description=(
                f"{len(no_tests)} passed mission(s) ran no tests: "
                f"{', '.join(m.mission_id for m in no_tests)}"
            ),
            mission_ids=[m.mission_id for m in no_tests],
            metric_value=len(no_tests),
            metric_unit="missions",
        ))

    # ── High read/write ratio ────────────────────────────────────────────────────
    high_read = [
        m for m in passed
        if m.files_written > 0 and (m.files_read / m.files_written) > _HIGH_READ_RATIO
    ]
    if high_read:
        avg_ratio = sum(m.files_read / m.files_written for m in high_read) / len(high_read)
        add(Bottleneck(
            severity="LOW",
            category="EXPLORATION",
            description=(
                f"{len(high_read)} mission(s) have high read/write ratio "
                f"(avg {avg_ratio:.1f}x) — repeated file scans before implementation"
            ),
            mission_ids=[m.mission_id for m in high_read],
            metric_value=avg_ratio,
            metric_unit="reads/write",
        ))

    # ── Low tools-per-turn ───────────────────────────────────────────────────────
    low_density = [
        m for m in passed
        if m.turns_used > 2 and (m.total_tool_calls / m.turns_used) < _LOW_TOOLS_PER_TURN
    ]
    if low_density:
        avg_density = sum(m.total_tool_calls / m.turns_used for m in low_density) / len(low_density)
        add(Bottleneck(
            severity="LOW",
            category="PARALLEL",
            description=(
                f"{len(low_density)} mission(s) average <{_LOW_TOOLS_PER_TURN} tools/turn "
                f"(avg {avg_density:.2f}) — many turns with single or no tool calls"
            ),
            mission_ids=[m.mission_id for m in low_density],
            metric_value=avg_density,
            metric_unit="tools/turn",
        ))

    # ── Memory not firing ───────────────────────────────────────────────────────
    no_preload = [m for m in run.missions if m.memory.speculative_preload_files == 0]
    no_exp = [m for m in run.missions if m.memory.experience_hits == 0]
    if len(no_preload) == len(run.missions):
        add(Bottleneck(
            severity="LOW",
            category="MEMORY",
            description="Speculative preload not firing on any mission — context may be loaded lazily",
            mission_ids=[m.mission_id for m in no_preload],
            metric_value=0.0,
            metric_unit="preload files",
        ))
    if len(no_exp) == len(run.missions):
        add(Bottleneck(
            severity="LOW",
            category="MEMORY",
            description="Experience engine hit rate is 0% — no reuse of prior successful strategies",
            mission_ids=[m.mission_id for m in no_exp],
            metric_value=0.0,
            metric_unit="experience hits",
        ))

    # ── High token-per-file ──────────────────────────────────────────────────────
    token_heavy = [
        m for m in passed
        if m.files_written > 0
        and (m.tokens_in + m.tokens_out) / m.files_written > _HIGH_TOKEN_PER_FILE
    ]
    if token_heavy:
        avg_tpf = sum((m.tokens_in + m.tokens_out) / m.files_written for m in token_heavy) / len(token_heavy)
        add(Bottleneck(
            severity="LOW",
            category="LLM",
            description=(
                f"{len(token_heavy)} mission(s) use >{_HIGH_TOKEN_PER_FILE/1000:.0f}k tokens/file "
                f"(avg {avg_tpf/1000:.1f}k) — context window may be growing unnecessarily"
            ),
            mission_ids=[m.mission_id for m in token_heavy],
            metric_value=avg_tpf / 1000,
            metric_unit="k tokens/file",
        ))

    return report
