#!/usr/bin/env python3
"""KRYTH Validation Runner — Phases 2-10.

Usage:
    python tests/run_validation.py                     # run all phases
    python tests/run_validation.py --phase 2           # single phase
    python tests/run_validation.py --report report.json

Phases that require a live LLM (5, 7, 8, 10) are skipped in dry-run mode
unless OPENAI_API_KEY is set.

Output: a validation dashboard with PASS/FAIL per phase + metrics.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src is on the path
_HERE = Path(__file__).parent
_SRC  = _HERE.parent / "src"
sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class PhaseResult:
    phase: int
    name: str
    passed: bool
    skipped: bool = False
    duration_s: float = 0.0
    metrics: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    tests_run: int = 0
    tests_passed: int = 0

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.passed else "FAIL"

    def short_summary(self) -> str:
        s = f"  Phase {self.phase} [{self.status}] {self.name} ({self.duration_s:.1f}s)"
        if self.tests_run:
            s += f" — {self.tests_passed}/{self.tests_run} tests"
        for k, v in self.metrics.items():
            s += f"\n     {k}: {v}"
        if self.errors:
            for e in self.errors[:3]:
                s += f"\n     ✗ {e}"
        return s


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------

def _run_pytest(test_file: str, extra_args: List[str] = None) -> PhaseResult:
    """Run a pytest file and parse the output."""
    test_path = str(_HERE / test_file)
    cmd = [sys.executable, "-m", "pytest", test_path, "--tb=short", "-q"] + (extra_args or [])
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        duration = time.monotonic() - t0
        output = proc.stdout + proc.stderr

        # Parse summary line: "X passed, Y failed, Z errors in Ns"
        passed = failed = errors_count = 0
        for line in output.splitlines():
            if "passed" in line or "failed" in line or "error" in line:
                import re
                m = re.findall(r"(\d+)\s+(passed|failed|error)", line)
                for count, kind in m:
                    if kind == "passed":
                        passed = int(count)
                    elif kind == "failed":
                        failed = int(count)
                    elif kind == "error":
                        errors_count = int(count)

        total = passed + failed + errors_count
        success = proc.returncode == 0

        error_lines = []
        if not success:
            for line in output.splitlines():
                if line.startswith("FAILED") or line.startswith("ERROR") or "AssertionError" in line:
                    error_lines.append(line[:120])

        return PhaseResult(
            phase=0,  # caller sets this
            name="",
            passed=success,
            duration_s=duration,
            tests_run=total,
            tests_passed=passed,
            errors=error_lines[:5],
        )
    except subprocess.TimeoutExpired:
        return PhaseResult(
            phase=0, name="", passed=False,
            duration_s=300.0, errors=["TIMEOUT after 300s"]
        )
    except Exception as exc:
        return PhaseResult(
            phase=0, name="", passed=False,
            duration_s=time.monotonic() - t0, errors=[str(exc)]
        )


def phase1_unit_tests() -> PhaseResult:
    r = _run_pytest("", extra_args=["C:/Users/navadeep/Documents/Kryth/kryth/tests"])
    r.phase = 1
    r.name = "Unit Test Validation (821 tests)"
    # Check specific targets
    r.metrics["target"] = "821/821"
    return r


def phase2_integration() -> PhaseResult:
    r = _run_pytest("test_integration.py")
    r.phase = 2
    r.name = "Full Integration Validation"
    return r


def phase3_parallel() -> PhaseResult:
    r = _run_pytest("test_parallel_metrics.py")
    r.phase = 3
    r.name = "Parallel Agent Validation"

    # If tests passed, extract metrics from metrics_collector if available
    if r.passed:
        r.metrics["peak_parallelism"] = "measured by test_eight_agent_parallel_peak_at_least_4"
        r.metrics["work_stealing"] = "validated"
        r.metrics["layer_ordering"] = "validated"
    return r


def phase4_memory() -> PhaseResult:
    r = _run_pytest("test_memory_manager.py")
    r.phase = 4
    r.name = "Memory Validation"
    if r.passed:
        r.metrics["ExecutionMemory"] = "PASS"
        r.metrics["WorkflowMemory"] = "PASS"
        r.metrics["FailureMemory"] = "PASS"
        r.metrics["DecisionMemory"] = "PASS"
        r.metrics["KnowledgeGraph"] = "PASS"
        r.metrics["ReflectionEngine"] = "PASS"
    return r


def phase5_browser() -> PhaseResult:
    has_api = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    if not has_api:
        return PhaseResult(
            phase=5, name="Browser Validation",
            passed=False, skipped=True,
            errors=["Requires live LLM backend (set OPENAI_API_KEY or ANTHROPIC_API_KEY)"]
        )
    r = _run_pytest("test_browser_profile.py")
    r.phase = 5
    r.name = "Browser Validation"
    return r


def phase6_supervisor() -> PhaseResult:
    r = _run_pytest("test_supervisor_chaos.py")
    r.phase = 6
    r.name = "Supervisor / Chaos Validation"
    if r.passed:
        r.metrics["repair_loop"] = "PASS"
        r.metrics["exception_isolation"] = "PASS"
        r.metrics["ownership_release"] = "PASS"
        r.metrics["context_isolation"] = "PASS"
    return r


def phase7_dashboard() -> PhaseResult:
    # Dashboard tests are part of integration
    r = _run_pytest("test_integration.py", extra_args=["-k", "Dashboard"])
    r.phase = 7
    r.name = "Dashboard Validation"
    return r


def phase8_long_run() -> PhaseResult:
    has_api = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    return PhaseResult(
        phase=8, name="Long Run Stability (2h/4h/8h)",
        passed=False, skipped=not has_api,
        errors=[] if has_api else ["Requires live LLM backend — run: kryth <mission> --duration 2h"],
        metrics={"min_target": "2 hours without crash"}
    )


def phase9_benchmarks() -> PhaseResult:
    t0 = time.monotonic()
    results = {}
    errors = []

    # Run the retrieval benchmarks
    try:
        sys.path.insert(0, str(_SRC))
        from tests.benchmarks import run_all_benchmarks  # type: ignore
        bench_results = run_all_benchmarks(str(_SRC.parent))
        for r in bench_results:
            results[r.name] = f"{r.avg_latency_ms:.1f}ms avg, {r.throughput_qps:.0f} q/s"
    except Exception as exc:
        errors.append(f"Benchmark error: {exc}")

    return PhaseResult(
        phase=9, name="Performance Benchmark",
        passed=not errors,
        duration_s=time.monotonic() - t0,
        metrics=results,
        errors=errors,
    )


def phase10_real_world() -> PhaseResult:
    has_api = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    return PhaseResult(
        phase=10, name="Real World Missions",
        passed=False, skipped=not has_api,
        errors=[] if has_api else ["Requires live LLM backend — run: kryth 'Build React app'"],
        metrics={
            "Mission A": "Build React application",
            "Mission B": "Build FastAPI backend",
            "Mission C": "Fix 100 bugs",
        }
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _print_dashboard(results: List[PhaseResult]) -> None:
    width = 72
    print()
    print("=" * width)
    print("  KRYTH VALIDATION DASHBOARD")
    print("=" * width)

    total_pass = sum(1 for r in results if r.passed and not r.skipped)
    total_skip = sum(1 for r in results if r.skipped)
    total_fail = sum(1 for r in results if not r.passed and not r.skipped)

    for r in results:
        icon = "✓" if r.passed else ("○" if r.skipped else "✗")
        status_str = r.status.ljust(4)
        line = f"  {icon} [{status_str}] Phase {r.phase:2d}: {r.name}"
        if r.tests_run:
            line += f"  ({r.tests_passed}/{r.tests_run})"
        print(line)
        for k, v in list(r.metrics.items())[:3]:
            print(f"              {k}: {v}")
        for e in r.errors[:2]:
            print(f"              ✗ {e}")

    print()
    print("-" * width)
    print(f"  PASS: {total_pass}   SKIP: {total_skip}   FAIL: {total_fail}")
    print("-" * width)

    # Success criteria
    criteria = [
        ("Unit Tests 100%", any(r.phase == 1 and r.passed for r in results)),
        ("Integration 100%", any(r.phase == 2 and r.passed for r in results)),
        ("Parallel Agents", any(r.phase == 3 and r.passed for r in results)),
        ("Memory Systems", any(r.phase == 4 and r.passed for r in results)),
        ("Chaos Recovery", any(r.phase == 6 and r.passed for r in results)),
    ]
    print()
    print("  SUCCESS CRITERIA:")
    all_met = True
    for name, met in criteria:
        icon = "✓" if met else "✗"
        if not met:
            all_met = False
        print(f"    {icon} {name}")

    print()
    if all_met:
        print("  ✓ All automated validation criteria met.")
        print("  ✓ Ready for live LLM phases (5, 8, 10).")
    else:
        print("  ✗ Some criteria not yet met — see failures above.")
    print("=" * width)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="KRYTH Phase 2-10 Validation Runner")
    parser.add_argument("--phase", type=int, default=0, help="Run a single phase (0=all)")
    parser.add_argument("--report", type=str, help="Save JSON report to file")
    args = parser.parse_args()

    phase_runners = {
        1: phase1_unit_tests,
        2: phase2_integration,
        3: phase3_parallel,
        4: phase4_memory,
        5: phase5_browser,
        6: phase6_supervisor,
        7: phase7_dashboard,
        8: phase8_long_run,
        9: phase9_benchmarks,
        10: phase10_real_world,
    }

    if args.phase:
        if args.phase not in phase_runners:
            print(f"Unknown phase: {args.phase}")
            return 1
        runners = {args.phase: phase_runners[args.phase]}
    else:
        runners = phase_runners

    results: List[PhaseResult] = []
    for phase_num, runner in sorted(runners.items()):
        print(f"Running Phase {phase_num}...", end=" ", flush=True)
        result = runner()
        result.phase = phase_num
        results.append(result)
        print(result.status)

    _print_dashboard(results)

    if args.report:
        report_data = [
            {
                "phase": r.phase,
                "name": r.name,
                "status": r.status,
                "passed": r.passed,
                "skipped": r.skipped,
                "duration_s": r.duration_s,
                "tests_run": r.tests_run,
                "tests_passed": r.tests_passed,
                "metrics": r.metrics,
                "errors": r.errors,
            }
            for r in results
        ]
        Path(args.report).write_text(json.dumps(report_data, indent=2))
        print(f"Report saved to {args.report}")

    failed = [r for r in results if not r.passed and not r.skipped]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
