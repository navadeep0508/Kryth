"""Generates text / Markdown / JSON reports from BenchmarkRun data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .benchmark_metrics import BenchmarkRun, MissionMetrics
from .benchmark_compare import RunComparison


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


def _bar(ratio: float, width: int = 20, fill: str = "█", empty: str = "░") -> str:
    filled = int(ratio * width)
    return fill * filled + empty * (width - filled)


# ── Mission-level section ─────────────────────────────────────────────────────

def _mission_section(m: MissionMetrics) -> list[str]:
    t = m.timings
    status = "PASS ✓" if m.success else "FAIL ✗"
    lines = [
        f"### [{m.mission_id}] {m.mission_name}",
        f"",
        f"**Status:** {status}   **Duration:** {_ms(t.duration_ms)}",
        f"",
        f"| Timing Milestone       | Value |",
        f"|------------------------|-------|",
        f"| First tool call        | {_ms(t.first_tool_call_ms)} |",
        f"| First file read        | {_ms(t.first_read_ms)} |",
        f"| First file write       | {_ms(t.first_write_ms)} |",
        f"| First stream begin     | {_ms(t.first_stream_begin_ms)} |",
        f"| First chunk written    | {_ms(t.first_chunk_ms)} |",
        f"| First test run         | {_ms(t.first_test_ms)} |",
        f"",
        f"| Metric                 | Value |",
        f"|------------------------|-------|",
        f"| Tokens in              | {_tok(m.tokens_in)} |",
        f"| Tokens out             | {_tok(m.tokens_out)} |",
        f"| Tool calls             | {m.total_tool_calls} |",
        f"| Files written          | {m.files_written} |",
        f"| Files read             | {m.files_read} |",
        f"| Commands run           | {m.commands_run} |",
        f"| Turns used             | {m.turns_used} |",
        f"",
        f"**Parallel:** {_pct(m.parallel.parallel_efficiency_pct)} efficiency  "
        f"({m.parallel.parallel_batches}/{m.parallel.total_tool_batches} batches parallel, "
        f"max batch={m.parallel.max_batch_size})",
        f"",
        f"**Streaming:** {m.streaming.streams_started} streams, "
        f"{m.streaming.total_chunks} chunks, "
        f"{m.streaming.total_lines_streamed} lines streamed",
        f"",
        f"**Memory:** {m.memory.speculative_preload_files} speculative files, "
        f"{m.memory.worker_pool_preload_files} worker-pool files",
        f"",
    ]
    if m.error:
        lines += [f"**Error:**", f"```", m.error[:500], f"```", f""]
    return lines


# ── Summary table ─────────────────────────────────────────────────────────────

def _summary_table(run: BenchmarkRun) -> list[str]:
    lines = [
        f"| ID | Mission | Status | Duration | First Write | Tokens | Parallel% |",
        f"|----|---------|--------|----------|-------------|--------|-----------|",
    ]
    for m in run.missions:
        status = "PASS" if m.success else "FAIL"
        lines.append(
            f"| {m.mission_id} | {m.mission_name[:30]} | {status} "
            f"| {_ms(m.timings.duration_ms)} "
            f"| {_ms(m.timings.first_write_ms)} "
            f"| {_tok(m.tokens_in + m.tokens_out)} "
            f"| {_pct(m.parallel.parallel_efficiency_pct)} |"
        )
    return lines


# ── Comparison section ────────────────────────────────────────────────────────

def _comparison_section(cmp: RunComparison) -> list[str]:
    lines = [
        f"## Comparison vs Previous Run",
        f"",
        f"**Previous:** `{cmp.old_run_id}` ({cmp.old_timestamp[:19]})",
        f"**Current:**  `{cmp.new_run_id}` ({cmp.new_timestamp[:19]})",
        f"",
        f"| Metric          | Delta |",
        f"|-----------------|-------|",
        f"| Pass rate       | {cmp.pass_rate_delta_pct:+.1f}% |",
        f"| Avg duration    | {cmp.avg_duration_delta_ms:+.0f}ms |",
        f"| Total tokens    | {cmp.total_tokens_delta:+d} |",
        f"",
    ]
    if cmp.regressions:
        lines.append(f"### ⚠ Regressions ({len(cmp.regressions)})")
        lines.append("")
        for d in cmp.regressions:
            arrow = "PASS→FAIL" if (d.old_success and not d.new_success) else ""
            lines.append(
                f"- **{d.mission_id}** {d.mission_name}: "
                f"{d.duration_delta_ms:+.0f}ms  {arrow}"
            )
        lines.append("")
    if cmp.improvements:
        lines.append(f"### ✓ Improvements ({len(cmp.improvements)})")
        lines.append("")
        for d in cmp.improvements:
            arrow = "FAIL→PASS" if (not d.old_success and d.new_success) else ""
            lines.append(
                f"- **{d.mission_id}** {d.mission_name}: "
                f"{d.duration_delta_ms:+.0f}ms  {arrow}"
            )
        lines.append("")
    return lines


# ── Top-level report generators ───────────────────────────────────────────────

def generate_markdown_report(
    run: BenchmarkRun,
    comparison: Optional[RunComparison] = None,
    output_path: Optional[str] = None,
) -> str:
    """Generate a full Markdown report.  Returns the string and optionally writes to file."""
    pass_bar = _bar(run.pass_rate_pct / 100)

    lines = [
        f"# KRYTH Benchmark Report",
        f"",
        f"**Run ID:** `{run.run_id}`  ",
        f"**Timestamp:** {run.timestamp[:19]}  ",
        f"**KRYTH Version:** {run.kryth_version}  ",
        f"**Timeout:** {run.timeout_s}s per mission  ",
        f"",
        f"## Summary",
        f"",
        f"| Metric         | Value |",
        f"|----------------|-------|",
        f"| Total missions | {run.total} |",
        f"| Passed         | {run.passed} |",
        f"| Failed         | {run.failed} |",
        f"| Pass rate      | {_pct(run.pass_rate_pct)} |",
        f"| Avg duration   | {_ms(run.avg_duration_ms)} |",
        f"| Total tokens   | {_tok(run.total_tokens)} |",
        f"",
        f"Pass rate: [{pass_bar}] {_pct(run.pass_rate_pct)}",
        f"",
        f"## Mission Results",
        f"",
    ]
    lines += _summary_table(run)
    lines.append("")

    if comparison:
        lines += _comparison_section(comparison)

    lines.append("## Per-Mission Details")
    lines.append("")
    for m in run.missions:
        lines += _mission_section(m)

    report = "\n".join(lines)

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(report, encoding="utf-8")

    return report


def generate_text_summary(run: BenchmarkRun) -> str:
    """A short plain-text summary for console output."""
    lines = [
        f"",
        f"{'═'*60}",
        f"  KRYTH BENCHMARK — {run.run_id}",
        f"{'═'*60}",
        f"  Passed: {run.passed}/{run.total}  ({_pct(run.pass_rate_pct)})",
        f"  Avg duration: {_ms(run.avg_duration_ms)}",
        f"  Total tokens: {_tok(run.total_tokens)}",
        f"{'─'*60}",
    ]
    for m in run.missions:
        status = "✓" if m.success else "✗"
        lines.append(
            f"  {status} [{m.mission_id}] {m.mission_name:<35} "
            f"{_ms(m.timings.duration_ms):>7}  "
            f"write@{_ms(m.timings.first_write_ms):>7}"
        )
    lines.append(f"{'═'*60}")
    lines.append("")
    return "\n".join(lines)


def save_json_report(run: BenchmarkRun, output_path: str) -> None:
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(run.to_dict(), fh, indent=2, default=str)


def generate_profiling_report(run: BenchmarkRun) -> str:
    """Auto-profiling: bottleneck, idle, memory, testing, scheduler analysis."""
    lines = [
        f"# KRYTH Profiling Report — {run.run_id}",
        f"",
        f"## Bottleneck Analysis",
        f"",
        f"| Mission | Duration | 1st Write | Tests? | Parallel% | Memory Hits |",
        f"|---------|----------|-----------|--------|-----------|-------------|",
    ]
    for m in run.missions:
        has_tests = m.timings.first_test_ms > 0
        lines.append(
            f"| {m.mission_id} {m.mission_name[:20]:<20} "
            f"| {_ms(m.timings.duration_ms):>8} "
            f"| {_ms(m.timings.first_write_ms):>9} "
            f"| {'YES' if has_tests else 'NO ':>6} "
            f"| {_pct(m.parallel.parallel_efficiency_pct):>9} "
            f"| {m.memory.experience_hits + m.memory.speculative_preload_files:>11} |"
        )

    # Bottleneck detection
    passed = [m for m in run.missions if m.success]
    failed = [m for m in run.missions if not m.success]

    lines += ["", "## Identified Bottlenecks", ""]
    if failed:
        lines.append(f"**FAILURES ({len(failed)}):**")
        for m in failed:
            lines.append(f"- [{m.mission_id}] {m.mission_name}: {m.error[:120]}")
        lines.append("")

    # Idle detection: large gap between first_tool_call and first_write
    idle_missions = [
        m for m in passed
        if m.timings.first_write_ms > 0 and
           (m.timings.first_write_ms - m.timings.first_tool_call_ms) > 30_000
    ]
    if idle_missions:
        lines.append("**Slow-to-write missions (>30s before first write):**")
        for m in idle_missions:
            gap = m.timings.first_write_ms - m.timings.first_tool_call_ms
            lines.append(f"- [{m.mission_id}] gap={_ms(gap)} — too many reads before writing")
        lines.append("")

    # Parallel efficiency
    low_par = [m for m in passed if m.parallel.parallel_efficiency_pct < 40]
    if low_par:
        lines.append("**Low parallel efficiency (<40%):**")
        for m in low_par:
            lines.append(
                f"- [{m.mission_id}] {_pct(m.parallel.parallel_efficiency_pct)} "
                f"({m.parallel.parallel_batches} parallel batches / {m.parallel.total_tool_batches} total)"
            )
        lines.append("")

    # Memory analysis
    lines += ["## Memory Analysis", ""]
    no_preload = [m for m in run.missions if m.memory.speculative_preload_files == 0]
    no_exp = [m for m in run.missions if m.memory.experience_hits == 0]
    lines.append(f"- Missions with speculative preload: {len(run.missions) - len(no_preload)}/{len(run.missions)}")
    lines.append(f"- Missions with experience hits: {len(run.missions) - len(no_exp)}/{len(run.missions)}")
    avg_exp = sum(m.memory.experience_hits for m in run.missions) / max(len(run.missions), 1)
    lines.append(f"- Avg experience hits per mission: {avg_exp:.1f}")
    lines.append("")

    # Testing analysis
    lines += ["## Testing Analysis", ""]
    no_tests = [m for m in passed if m.timings.first_test_ms < 0]
    with_tests = [m for m in passed if m.timings.first_test_ms > 0]
    lines.append(f"- Missions that ran tests: {len(with_tests)}/{len(passed)}")
    if no_tests:
        lines.append(f"- Missions with NO test runs: {', '.join(m.mission_id for m in no_tests)}")
    if with_tests:
        avg_test_ms = sum(m.timings.first_test_ms for m in with_tests) / len(with_tests)
        lines.append(f"- Avg time to first test: {_ms(avg_test_ms)}")
    lines.append("")

    # Recommendations
    lines += ["## Optimization Recommendations", ""]
    recs = []
    if no_tests:
        recs.append("TESTING: Missions without test runs — ensure system prompt requires test file generation")
    avg_par = sum(m.parallel.parallel_efficiency_pct for m in passed) / max(len(passed), 1) if passed else 0
    if avg_par < 50:
        recs.append(f"PARALLEL: Avg efficiency {_pct(avg_par)} — increase parallel tool batching")
    avg_mem = sum(m.memory.speculative_preload_files + m.memory.worker_pool_preload_files for m in run.missions) / max(len(run.missions), 1)
    if avg_mem < 3:
        recs.append("MEMORY: Low preload file count — verify _speculative_preload is firing")
    if len(failed) > 0:
        recs.append(f"SCALING: {len(failed)} timeout(s) — enable multi-agent for complex missions")
    if not recs:
        recs.append("No critical bottlenecks detected — system performing well")
    for r in recs:
        lines.append(f"- {r}")

    return "\n".join(lines)
