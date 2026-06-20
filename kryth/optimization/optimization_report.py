"""Formats the Autonomous Engineering Report from all analysis results."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from benchmark.benchmark_metrics import BenchmarkRun
from benchmark.benchmark_compare import RunComparison
from optimization.execution_profiler import ExecutionProfile
from optimization.bottleneck_detector import BottleneckReport
from optimization.worker_analyzer import WorkerAnalysis
from optimization.memory_analyzer import MemoryAnalysis
from optimization.llm_analyzer import LLMAnalysis
from optimization.tool_analyzer import ToolAnalysis
from optimization.scheduler_analyzer import SchedulerAnalysis
from optimization.recommendation_engine import RecommendationSet


# ── Formatting helpers ────────────────────────────────────────────────────────

def _ms(v: float) -> str:
    if v < 0:
        return "n/a"
    if v >= 60_000:
        return f"{v/60_000:.1f}m"
    if v >= 1_000:
        return f"{v/1_000:.1f}s"
    return f"{v:.0f}ms"


def _pct(v: float) -> str:
    return f"{v:.1f}%"


def _tok(v: int) -> str:
    if v >= 1_000_000:
        return f"{v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v/1_000:.1f}k"
    return str(v)


def _bar(ratio: float, width: int = 24) -> str:
    ratio = max(0.0, min(1.0, ratio))
    filled = int(ratio * width)
    return "█" * filled + "░" * (width - filled)


# ── Section builders ──────────────────────────────────────────────────────────

def _section_header(title: str) -> list[str]:
    return [f"## {title}", ""]


def _executive_summary(
    run: BenchmarkRun,
    profile: ExecutionProfile,
    recs: RecommendationSet,
    comparison: Optional[RunComparison],
) -> list[str]:
    pass_bar = _bar(run.pass_rate_pct / 100)
    lines = [
        f"**Pass Rate:** `[{pass_bar}]` {run.passed}/{run.total} ({_pct(run.pass_rate_pct)})",
        f"**Avg Duration:** {_ms(run.avg_duration_ms)}  |  "
        f"**Total Tokens:** {_tok(run.total_tokens)}  |  "
        f"**Run ID:** `{run.run_id}`",
        "",
    ]

    if run.passed == run.total:
        lines.append("All missions passed. Analysis focuses on speed, efficiency, and resource usage.")
    else:
        failed = [m for m in run.missions if not m.success]
        lines.append(
            f"**{len(failed)} mission(s) failed:** "
            + ", ".join(f"`{m.mission_id}`" for m in failed)
        )
    lines.append("")

    if recs.recommendations:
        top = recs.sorted()[:3]
        lines.append("**Top issues identified:**")
        for i, r in enumerate(top, 1):
            lines.append(f"{i}. [{r.priority}] {r.text}")
        lines.append("")

    if recs.total_expected_gain_pct > 0:
        lines.append(
            f"**Estimated gain if HIGH recommendations applied:** "
            f"~{recs.total_expected_gain_pct:.0f}% reduction in avg mission time  "
            f"(confidence: {_pct(recs.avg_confidence * 100)})"
        )
        lines.append("")

    if comparison:
        delta = comparison.pass_rate_delta_pct
        dur_delta = comparison.avg_duration_delta_ms
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"**vs Previous Run:** pass rate {sign}{delta:.1f}%  |  "
            f"avg duration {dur_delta:+.0f}ms  |  "
            f"tokens {comparison.total_tokens_delta:+,}"
        )
        lines.append("")

    return lines


def _what_was_fast(run: BenchmarkRun, profile: ExecutionProfile) -> list[str]:
    lines = []
    passed = [m for m in run.missions if m.success]
    if not passed:
        return lines

    # Fastest missions
    by_dur = sorted(passed, key=lambda m: m.timings.duration_ms)
    fastest = by_dur[:3]
    lines.append("**Fastest missions:**")
    for m in fastest:
        mp = next((p for p in profile.missions if p.mission_id == m.mission_id), None)
        note = ""
        if mp and mp.tools_per_turn > 2.5:
            note = " (high parallel density)"
        elif mp and mp.planning_ms < 5000:
            note = " (immediate execution)"
        lines.append(f"- `{m.mission_id}` {m.mission_name}: {_ms(m.timings.duration_ms)}{note}")
    lines.append("")

    # Best parallel efficiency
    by_par = sorted(passed, key=lambda m: m.parallel.parallel_efficiency_pct, reverse=True)
    if by_par:
        best = by_par[0]
        lines.append(
            f"**Best parallelism:** `{best.mission_id}` at "
            f"{_pct(best.parallel.parallel_efficiency_pct)} efficiency "
            f"(max batch size: {best.parallel.max_batch_size})"
        )
    lines.append("")
    return lines


def _what_was_slow(run: BenchmarkRun, profile: ExecutionProfile) -> list[str]:
    lines = []
    passed = [m for m in run.missions if m.success]
    failed = [m for m in run.missions if not m.success]

    if failed:
        lines.append("**Failed missions:**")
        for m in failed:
            lines.append(f"- `{m.mission_id}` {m.mission_name}: {m.error[:100] if m.error else 'unknown'}")
        lines.append("")

    # Slowest passed missions
    if passed:
        by_dur = sorted(passed, key=lambda m: m.timings.duration_ms, reverse=True)
        slow = by_dur[:3]
        lines.append("**Slowest passed missions:**")
        for m in slow:
            mp = next((p for p in profile.missions if p.mission_id == m.mission_id), None)
            reasons = []
            if mp and mp.planning_ms > 15_000:
                reasons.append(f"planning: {_ms(mp.planning_ms)}")
            if mp and mp.exploration_ms > 30_000:
                reasons.append(f"exploration: {_ms(mp.exploration_ms)}")
            if "api_error" in (m.error or "").lower() or "retry" in (m.error or "").lower():
                reasons.append("api_error retries")
            reason_str = f" — {', '.join(reasons)}" if reasons else ""
            lines.append(f"- `{m.mission_id}` {m.mission_name}: {_ms(m.timings.duration_ms)}{reason_str}")
        lines.append("")

    return lines


def _bottleneck_section(bn: BottleneckReport) -> list[str]:
    lines = []
    ranked = bn.sorted_by_severity()
    if not ranked:
        lines.append("No critical bottlenecks detected.")
        lines.append("")
        return lines
    for i, b in enumerate(ranked, 1):
        icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪"}.get(b.severity, "·")
        lines.append(f"{i}. **[{b.severity}]** `{b.category}` — {b.description}")
        if b.mission_ids:
            lines.append(f"   *Missions:* {', '.join(b.mission_ids)}")
    lines.append("")
    return lines


def _phase_breakdown(profile: ExecutionProfile) -> list[str]:
    lines = [
        f"| Phase          | Avg Duration | Avg % of Total |",
        f"|----------------|--------------|----------------|",
        f"| Planning       | {_ms(profile.avg_planning_ms):>12} | {_pct(profile.avg_planning_pct):>14} |",
        f"| Exploration    | {_ms(profile.avg_exploration_ms):>12} | {_pct(profile.avg_exploration_pct):>14} |",
        f"| Implementation | {_ms(profile.avg_implementation_ms):>12} | {_pct(profile.avg_implementation_pct):>14} |",
        f"",
        f"| Metric                | Value |",
        f"|-----------------------|-------|",
        f"| Avg read/write ratio  | {profile.avg_read_write_ratio:.1f}x |",
        f"| Avg tools per turn    | {profile.avg_tools_per_turn:.2f} |",
        f"| Missions with tests   | {profile.missions_with_tests}/{len(profile.missions)} |",
        f"| Missions streaming    | {profile.missions_streaming}/{len(profile.missions)} |",
        f"| Missions multi-agent  | {profile.missions_multi_agent}/{len(profile.missions)} |",
        "",
    ]
    return lines


def _worker_section(w: WorkerAnalysis) -> list[str]:
    lines = [
        f"| Metric                | Value |",
        f"|-----------------------|-------|",
        f"| Avg peak active       | {w.avg_peak_active:.1f} agents |",
        f"| Avg utilization       | {_pct(w.avg_utilization_pct)} |",
        f"| Total work steals     | {w.total_work_steals} |",
        f"| Single-agent missions | {w.missions_single_agent}/{w.missions_single_agent + w.missions_multi_agent} |",
        f"| Multi-agent missions  | {w.missions_multi_agent}/{w.missions_single_agent + w.missions_multi_agent} |",
        f"| Missions with idle slots | {w.missions_idle_waste} |",
        "",
    ]
    if w.under_provisioned:
        lines.append(f"⚠ Under-provisioned: {', '.join(w.under_provisioned)}")
    if w.over_provisioned:
        lines.append(f"⚠ Over-provisioned: {', '.join(w.over_provisioned)}")
    if w.under_provisioned or w.over_provisioned:
        lines.append("")
    return lines


def _memory_section(m: MemoryAnalysis) -> list[str]:
    lines = [
        f"| Memory Type          | Active Missions | Hit Rate |",
        f"|----------------------|-----------------|----------|",
        f"| Speculative preload  | {m.speculative_active_count}/{m.speculative_active_count + len([x for x in m.per_mission if x.speculative_preload_files == 0])} | {_pct(m.speculative_hit_rate * 100)} |",
        f"| Experience engine    | {m.experience_active_count}/{len(m.per_mission)} | {_pct(m.experience_hit_rate * 100)} |",
        f"| Worker pool preload  | {m.worker_pool_active_count}/{len(m.per_mission)} | — |",
        f"| Knowledge graph      | {'Active' if m.total_graph_hits > 0 else 'Inactive'} | — |",
        "",
        f"Total preload files: {m.total_preload_files}  |  "
        f"Total experience hits: {m.total_experience_hits}  |  "
        f"Total graph hits: {m.total_graph_hits}",
        "",
    ]
    if m.unused_memory_types:
        lines.append(f"⚠ Inactive memory subsystems: {', '.join(m.unused_memory_types)}")
        lines.append("")
    return lines


def _llm_section(l: LLMAnalysis) -> list[str]:
    lines = [
        f"| LLM Metric              | Value |",
        f"|-------------------------|-------|",
        f"| Total tokens in         | {_tok(l.total_tokens_in)} |",
        f"| Total tokens out        | {_tok(l.total_tokens_out)} |",
        f"| Total turns             | {l.total_turns} |",
        f"| Avg tokens per turn     | {l.avg_tokens_per_turn:,.0f} |",
        f"| Avg context ratio       | {_pct(l.avg_context_ratio * 100)} |",
        f"| Avg tools per turn      | {l.avg_tools_per_turn:.2f} |",
        f"| Avg silent turns/mission| {l.avg_silent_turns:.1f} |",
        f"| Missions with api_error | {l.missions_with_api_error} |",
        f"| Missions with timeout   | {l.missions_with_timeout} |",
        "",
    ]
    flags = []
    if l.context_bloat_detected:
        flags.append("⚠ Context bloat detected (input ratio > 85%)")
    if l.low_tool_density:
        flags.append("⚠ Low tool density (avg < 1.5 tools/turn)")
    if l.high_silent_turns:
        flags.append(f"⚠ High silent turns (avg {l.avg_silent_turns:.1f}/mission)")
    for f in flags:
        lines.append(f)
    if flags:
        lines.append("")
    return lines


def _scheduler_section(s: SchedulerAnalysis) -> list[str]:
    health = "✓ Healthy" if s.batch_scheduling_healthy else "⚠ Needs improvement"
    lines = [
        f"| Scheduler Metric        | Value |",
        f"|-------------------------|-------|",
        f"| Avg parallel efficiency | {_pct(s.avg_parallel_efficiency_pct)} |",
        f"| Avg parallelism ratio   | {_pct(s.avg_parallelism_ratio * 100)} |",
        f"| Avg max batch size      | {s.avg_max_batch_size:.1f} tools |",
        f"| Total parallel calls    | {s.total_parallel_calls} |",
        f"| Best mission            | {s.best_mission} |",
        f"| Worst mission           | {s.worst_mission} |",
        f"| Scheduling health       | {health} |",
        "",
    ]
    if s.serial_dominant:
        lines.append("⚠ Serial-dominant scheduling: most tool turns dispatch only 1 tool.")
        lines.append("")
    return lines


def _recommendations_section(recs: RecommendationSet) -> list[str]:
    lines = []
    sorted_recs = recs.sorted()
    if not sorted_recs:
        lines.append("No actionable recommendations — system performing well.")
        lines.append("")
        return lines
    for i, r in enumerate(sorted_recs, 1):
        lines.append(
            f"### {i}. [{r.priority}] {r.category} — {r.text}"
        )
        lines.append(f"**Observed:** {r.details}")
        lines.append(f"**Action:** {r.action}")
        if r.expected_gain_pct > 0:
            lines.append(
                f"**Expected gain:** ~{r.expected_gain_pct:.0f}% reduction in mission time  "
                f"*(confidence: {_pct(r.confidence * 100)})*"
            )
        lines.append("")
    return lines


def _version_comparison(comparison: RunComparison) -> list[str]:
    lines = [
        f"| Metric       | Delta |",
        f"|--------------|-------|",
        f"| Pass rate    | {comparison.pass_rate_delta_pct:+.1f}% |",
        f"| Avg duration | {comparison.avg_duration_delta_ms:+.0f}ms |",
        f"| Total tokens | {comparison.total_tokens_delta:+,} |",
        "",
    ]
    if comparison.regressions:
        lines.append(f"**Regressions ({len(comparison.regressions)}):**")
        for d in comparison.regressions:
            arrow = " (PASS→FAIL)" if d.old_success and not d.new_success else ""
            lines.append(f"- `{d.mission_id}` {d.mission_name}: {d.duration_delta_ms:+.0f}ms{arrow}")
        lines.append("")
    if comparison.improvements:
        lines.append(f"**Improvements ({len(comparison.improvements)}):**")
        for d in comparison.improvements:
            arrow = " (FAIL→PASS)" if not d.old_success and d.new_success else ""
            lines.append(f"- `{d.mission_id}` {d.mission_name}: {d.duration_delta_ms:+.0f}ms{arrow}")
        lines.append("")
    return lines


# ── Top-level report generator ────────────────────────────────────────────────

def generate_optimization_report(
    run: BenchmarkRun,
    profile: ExecutionProfile,
    bottlenecks: BottleneckReport,
    workers: WorkerAnalysis,
    memory: MemoryAnalysis,
    llm: LLMAnalysis,
    tools: ToolAnalysis,
    scheduler: SchedulerAnalysis,
    recs: RecommendationSet,
    comparison: Optional[RunComparison] = None,
    output_path: Optional[str] = None,
) -> str:
    lines = [
        f"# KRYTH Autonomous Engineering Report",
        f"",
        f"**Run:** `{run.run_id}`  |  **Timestamp:** {run.timestamp[:19]}  |  **Version:** {run.kryth_version}",
        f"",
        "---",
        "",
    ]

    lines += _section_header("Executive Summary")
    lines += _executive_summary(run, profile, recs, comparison)

    lines += _section_header("What Was Fast")
    lines += _what_was_fast(run, profile)

    lines += _section_header("What Was Slow")
    lines += _what_was_slow(run, profile)

    lines += _section_header("Bottleneck Ranking")
    lines += _bottleneck_section(bottlenecks)

    lines += _section_header("Phase Breakdown")
    lines += _phase_breakdown(profile)

    lines += _section_header("Scheduler Analysis")
    lines += _scheduler_section(scheduler)

    lines += _section_header("Worker Analysis")
    lines += _worker_section(workers)

    lines += _section_header("Memory Analysis")
    lines += _memory_section(memory)

    lines += _section_header("LLM Analysis")
    lines += _llm_section(llm)

    lines += _section_header("Recommendations")
    lines += _recommendations_section(recs)

    if comparison:
        lines += _section_header("Version Comparison")
        lines += _version_comparison(comparison)

    lines += [
        "---",
        "",
        "*This report was generated automatically by the KRYTH Autonomous Self-Optimization Engine.*",
        "*The engine only measures and recommends — it does not modify any code.*",
        "",
    ]

    report = "\n".join(lines)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report, encoding="utf-8")
    return report
