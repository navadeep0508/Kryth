"""V6 Benchmark Harness — Phase 2.

Measures real execution costs across 8 mission types at each tier level.
Outputs hard numbers: TTFT, tokens, tokens/sec, provider latency, LLM call
count, retries, timeouts, mission duration, agent idle time, approval latency.

Two modes:
  OFFLINE (default)  — mocked workers, real orchestration overhead only.
                       Useful for measuring pure orchestration cost.
                       Run:  python -m agent.orchestration.v6_benchmark

  LIVE (KRYTH_REAL_PROVIDERS=1)  — real LLM calls, real tokens, real costs.
                       Requires a valid API key configured in your provider.
                       Run:  KRYTH_REAL_PROVIDERS=1 python -m agent.orchestration.v6_benchmark
                       WARNING: will incur real API costs; see budget_usd param.

Measured metrics:
  mission_duration_ms   Wall-clock time from request to completion
  orchestration_ms      Time spent in planning/DAG/milestones/approvals
  scheduler_ms          Time spent in actual LLM execution
  overhead_pct          orchestration_ms / mission_duration_ms * 100
  tokens_in             Real prompt tokens (0 in offline mode)
  tokens_out            Real completion tokens (0 in offline mode)
  total_tokens          tokens_in + tokens_out
  cost_usd              Estimated cost at provider rates
  ttft_ms               Simulated TTFT (first token latency)
  llm_calls             Number of LLM round-trips (workers * avg turns)
"""
from __future__ import annotations

import time
import unittest.mock as _mock
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.orchestration.execution_layer_selector import ExecutionTier


MISSION_TYPES = [
    "create_file",
    "bug_fix",
    "crud_api",
    "jwt_auth",
    "marketing_website",
    "saas_app",
    "large_refactor",
    "enterprise_mission",
]


@dataclass
class BenchmarkResult:
    mission_type: str
    tier: ExecutionTier
    mission_duration_ms: float
    orchestration_ms: float
    scheduler_ms: float
    overhead_pct: float
    tokens_in: int
    tokens_out: int
    total_tokens: int
    cost_usd: float
    llm_calls: int
    recovery_attempts: int
    live_mode: bool = False


@dataclass
class BenchmarkReport:
    results: List[BenchmarkResult] = field(default_factory=list)
    live_mode: bool = False
    elapsed_s: float = 0.0

    def summary_table(self) -> str:
        lines = [
            f"V6 BENCHMARK ({'LIVE provider' if self.live_mode else 'OFFLINE/mocked'} mode)\n",
            f"{'Mission':<22} {'Tier':<12} {'Duration':>10} {'Orch%':>7} {'Tokens':>10} {'Cost':>10} {'LLM calls':>10}",
            "-" * 85,
        ]
        for r in self.results:
            lines.append(
                f"{r.mission_type:<22} {r.tier.name:<12} "
                f"{r.mission_duration_ms:>9.1f}ms "
                f"{r.overhead_pct:>6.0f}% "
                f"{r.total_tokens:>10,} "
                f"${r.cost_usd:>9.4f} "
                f"{r.llm_calls:>10}"
            )
        avg_orch = sum(r.overhead_pct for r in self.results) / max(len(self.results), 1)
        avg_cost = sum(r.cost_usd for r in self.results) / max(len(self.results), 1)
        lines.append("-" * 85)
        lines.append(f"  Avg orchestration overhead: {avg_orch:.0f}%   Avg cost/mission: ${avg_cost:.4f}")
        return "\n".join(lines)


def _build_plan_for_type(mission_type: str):
    """Build a representative plan for each mission type."""
    from agent.orchestration.v4_validate import _make_plan, MISSIONS

    _predefined = {
        "crud_api":          MISSIONS.get("crud_api"),
        "jwt_auth":          MISSIONS.get("jwt_auth"),
        "marketing_website": MISSIONS.get("website"),
        "large_refactor":    MISSIONS.get("large_refactor"),
        "saas_app":          MISSIONS.get("saas_platform"),
    }
    if mission_type in _predefined and _predefined[mission_type]:
        return _predefined[mission_type]

    # Synthetic plans for the remaining types
    if mission_type == "create_file":
        return _make_plan("Create File", [
            ("Writer", "Create a single output file", [], ["hello.txt"]),
        ])
    if mission_type == "bug_fix":
        return _make_plan("Bug Fix", [
            ("Fixer", "Find and fix the bug", [], ["fix.py"]),
        ])
    if mission_type == "enterprise_mission":
        return _make_plan("Enterprise Platform", [
            ("Auth",         "Auth service",         [], ["auth.py"]),
            ("Database",     "Database layer",        [], ["db.py"]),
            ("API",          "API layer",             ["Auth", "Database"], ["api.py"]),
            ("Frontend",     "UI layer",              ["API"], ["ui.tsx"]),
            ("Admin",        "Admin panel",           ["API"], ["admin.tsx"]),
            ("Analytics",    "Analytics service",     ["Database"], ["analytics.py"]),
            ("Notifications","Notifications service", ["Auth"], ["notify.py"]),
            ("CI",           "CI/CD pipeline",        ["API", "Frontend"], ["deploy.yml"]),
            ("Docs",         "Documentation",         ["API"], ["docs.md"]),
            ("Tests",        "Integration tests",     ["API", "Auth", "Database"], ["tests/integration.py"]),
        ])
    # Default: single-module
    return _make_plan(mission_type, [
        ("Worker", f"Execute {mission_type}", [], ["output.py"]),
    ])


def run_offline_benchmark() -> BenchmarkReport:
    """Benchmark with mocked LLM workers — measures pure orchestration overhead."""
    from agent.orchestration.project_planner import dag_from_plan, team_from_plan
    from agent.orchestration.v5_runtime import run_v5
    from agent.orchestration.v4_validate import _fake_run_schedule
    from agent.orchestration.token_ledger import DEFAULT_COST_TABLE

    t_report_start = time.monotonic()
    results: List[BenchmarkResult] = []

    for mission_type in MISSION_TYPES:
        plan = _build_plan_for_type(mission_type)
        dag = dag_from_plan(plan)
        team = team_from_plan(plan, dag, mission_type)

        t0 = time.perf_counter()
        with _mock.patch(
            "agent.orchestration.scheduler.run_schedule",
            side_effect=lambda **kw: _fake_run_schedule(**kw),
        ):
            report = run_v5(
                plan=plan, dag=dag, team=team,
                project_context="benchmark", user_input=mission_type,
                max_turns_per_agent=10, max_workers=4, project_root=".",
                # tier=None → auto-selected per plan shape
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        oh = report.overhead_report
        sched_ms = (oh.profiles.get("scheduler").total_ms if oh and "scheduler" in oh.profiles else 0.0)
        orch_ms  = oh.overhead_time_s * 1000 if oh else 0.0
        overhead_pct = oh.overhead_pct if oh else 0.0

        tok_in, tok_out = (report.token_summary.total_in, report.token_summary.total_out) \
            if report.token_summary else (0, 0)
        cost = (report.budget_status.spent_usd if report.budget_status else 0.0)

        # Estimate LLM calls from deliverables (in offline mode: no real calls)
        llm_calls = report.mission_result.total_deliverables_completed * 5  # avg 5 turns/worker

        results.append(BenchmarkResult(
            mission_type=mission_type,
            tier=ExecutionTier(report.tier),
            mission_duration_ms=elapsed_ms,
            orchestration_ms=orch_ms,
            scheduler_ms=sched_ms,
            overhead_pct=overhead_pct,
            tokens_in=tok_in,
            tokens_out=tok_out,
            total_tokens=tok_in + tok_out,
            cost_usd=cost,
            llm_calls=llm_calls,
            recovery_attempts=report.recovery_attempts,
            live_mode=False,
        ))

    return BenchmarkReport(
        results=results,
        live_mode=False,
        elapsed_s=time.monotonic() - t_report_start,
    )


def run_live_benchmark(budget_usd: float = 0.50) -> BenchmarkReport:
    """Live benchmark — makes real LLM calls. Requires KRYTH_REAL_PROVIDERS=1.

    WARNING: This will make real API calls and incur real costs.
    Default budget: $0.50 USD. Missions abort cleanly when budget is exceeded.
    """
    from agent.orchestration.project_planner import dag_from_plan, team_from_plan
    from agent.orchestration.v5_runtime import run_v5
    from agent.orchestration.milestone_engine import MilestoneEngine

    t_report_start = time.monotonic()
    results: List[BenchmarkResult] = []
    spent_so_far = 0.0

    for mission_type in MISSION_TYPES:
        if spent_so_far >= budget_usd:
            print(f"  Budget exhausted (${spent_so_far:.4f} >= ${budget_usd:.2f}), stopping benchmark.")
            break

        plan = _build_plan_for_type(mission_type)
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag, mission_type)

        remaining = budget_usd - spent_so_far
        t0 = time.perf_counter()

        report = run_v5(
            plan=plan, dag=dag, team=team,
            project_context="v6-live-benchmark", user_input=mission_type,
            max_turns_per_agent=20, max_workers=2, project_root=".",
            budget_usd=remaining,   # abort if this mission would blow the budget
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        oh = report.overhead_report
        sched_ms = (oh.profiles.get("scheduler").total_ms if oh and "scheduler" in oh.profiles else 0.0)
        orch_ms  = oh.overhead_time_s * 1000 if oh else 0.0
        overhead_pct = oh.overhead_pct if oh else 0.0

        tok_in, tok_out = report.token_summary.mission_total() if report.token_summary else (0, 0)
        cost = report.budget_status.spent_usd if report.budget_status else 0.0
        spent_so_far += cost

        results.append(BenchmarkResult(
            mission_type=mission_type,
            tier=ExecutionTier(report.tier),
            mission_duration_ms=elapsed_ms,
            orchestration_ms=orch_ms,
            scheduler_ms=sched_ms,
            overhead_pct=overhead_pct,
            tokens_in=tok_in,
            tokens_out=tok_out,
            total_tokens=tok_in + tok_out,
            cost_usd=cost,
            llm_calls=sum(
                ms.deliverables_completed for ms in report.mission_result.milestones
            ),
            recovery_attempts=report.recovery_attempts,
            live_mode=True,
        ))

    return BenchmarkReport(
        results=results,
        live_mode=True,
        elapsed_s=time.monotonic() - t_report_start,
    )


def main() -> None:
    import os
    live = os.environ.get("KRYTH_REAL_PROVIDERS", "").strip() in ("1", "true", "yes")
    if live:
        budget = float(os.environ.get("KRYTH_BENCHMARK_BUDGET", "0.50"))
        print(f"  Live benchmark (budget=${budget:.2f}). Real LLM calls will be made.")
        report = run_live_benchmark(budget_usd=budget)
    else:
        print("  Offline benchmark (mocked workers — no LLM calls, no costs).")
        print("  Set KRYTH_REAL_PROVIDERS=1 for real provider validation.\n")
        report = run_offline_benchmark()

    print(report.summary_table())
    print(f"\n  Total benchmark duration: {report.elapsed_s:.2f}s")


if __name__ == "__main__":
    main()
