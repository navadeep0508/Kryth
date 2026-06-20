"""V5 Benchmark — Phase 13.

Runs every v4_validate.py mission through BOTH the bare V4 path
(MilestoneEngine.run() directly) and the full V5 path (run_v5()), and
reports hard numbers side by side:

  Mission Time · Worker Utilization · Lead Utilization · Approval Latency
  Critical Path Duration · Token Usage · Cost · Rework Rate · Validation Success

Several metrics simply do not exist in V4 (worker/lead utilization,
approval latency, recovery, quality score, forecast) — that absence IS
part of the V4 vs V5 finding, so they are reported as "not tracked" rather
than estimated.

No LLM calls — same offline stub pattern as v4_validate.py / v5_validate.py.

Usage:
    python -m agent.orchestration.v5_benchmark
"""
from __future__ import annotations

import time
import unittest.mock as _mock
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.orchestration.v4_validate import MISSIONS, _fake_run_schedule
from agent.orchestration.project_planner import dag_from_plan, team_from_plan
from agent.orchestration.milestone_engine import MilestoneEngine
from agent.orchestration.v5_runtime import run_v5
from agent.orchestration.org_health import OrgHealthTracker

_TOKENS_PER_DELIVERABLE = 3000
_COST_PER_1K_TOKENS = 1.0


@dataclass
class ArmResult:
    mission_time_s: float
    critical_path_duration_s: float
    token_usage: int
    cost: float
    rework_rate: float
    validation_success_rate: float
    worker_utilization: Optional[float] = None     # None = not tracked
    lead_utilization: Optional[float] = None
    approval_latency_s: Optional[float] = None
    quality_score: Optional[float] = None
    recovery_attempts: Optional[int] = None
    forecast_confidence: Optional[str] = None


def _rework_rate(mission_result) -> float:
    ms = mission_result.milestones
    if not ms:
        return 0.0
    return sum(1 for m in ms if getattr(m, "rework_required", False)) / len(ms)


def _validation_success_rate(mission_result) -> float:
    total = passed = 0
    for ms in mission_result.milestones:
        for v in ms.contract_validations.values():
            total += 1
            if getattr(v, "passed", False):
                passed += 1
    return passed / total if total else 1.0


def _run_v4_arm(plan) -> ArmResult:
    dag = dag_from_plan(plan)
    team = team_from_plan(plan, dag, plan.project_name)

    t0 = time.perf_counter()
    with _mock.patch("agent.orchestration.scheduler.run_schedule", side_effect=lambda **kw: _fake_run_schedule(**kw)):
        engine = MilestoneEngine(
            plan=plan, dag=dag, team=team, project_context="bench", user_input=plan.project_name,
            max_turns_per_agent=10, max_workers=2, project_root=".",
        )
        result = engine.run()
    elapsed = time.perf_counter() - t0

    tokens = result.total_deliverables_completed * _TOKENS_PER_DELIVERABLE
    return ArmResult(
        mission_time_s=elapsed,
        critical_path_duration_s=result.critical_path_duration_s,
        token_usage=tokens,
        cost=tokens / 1000 * _COST_PER_1K_TOKENS,
        rework_rate=_rework_rate(result),
        validation_success_rate=_validation_success_rate(result),
        # V4 does not track these at all — that absence is the finding.
        worker_utilization=None,
        lead_utilization=None,
        approval_latency_s=None,
        quality_score=None,
        recovery_attempts=None,
        forecast_confidence=None,
    )


def _run_v5_arm(plan) -> ArmResult:
    dag = dag_from_plan(plan)
    team = team_from_plan(plan, dag, plan.project_name)

    t0 = time.perf_counter()
    with _mock.patch("agent.orchestration.scheduler.run_schedule", side_effect=lambda **kw: _fake_run_schedule(**kw)):
        report = run_v5(
            plan=plan, dag=dag, team=team, project_context="bench", user_input=plan.project_name,
            max_turns_per_agent=10, max_workers=2, project_root=".",
            tier=4,  # Benchmark always uses full ENTERPRISE stack for a fair V4/V5 comparison
        )
    elapsed = time.perf_counter() - t0
    result = report.mission_result

    tokens = result.total_deliverables_completed * _TOKENS_PER_DELIVERABLE
    oh = report.org_health_report
    lead_util = None
    if oh and oh.lead_records:
        lead_util = sum(l.approval_rate for l in oh.lead_records) / len(oh.lead_records)

    return ArmResult(
        mission_time_s=elapsed,
        critical_path_duration_s=result.critical_path_duration_s,
        token_usage=tokens,
        cost=tokens / 1000 * _COST_PER_1K_TOKENS,
        rework_rate=_rework_rate(result),
        validation_success_rate=_validation_success_rate(result),
        worker_utilization=oh.overall_utilization if oh else None,
        lead_utilization=lead_util,
        approval_latency_s=oh.avg_approval_latency_s if oh else None,
        quality_score=report.quality_report.mission_quality_score if report.quality_report else None,
        recovery_attempts=report.recovery_attempts,
        forecast_confidence=report.forecast.confidence if report.forecast else None,
    )


def _fmt(val, suffix="") -> str:
    if val is None:
        return "not tracked"
    if isinstance(val, float):
        return f"{val:.2f}{suffix}"
    return f"{val}{suffix}"


def run_benchmark() -> Dict[str, Dict[str, ArmResult]]:
    rows: Dict[str, Dict[str, ArmResult]] = {}
    print("◈ V5 BENCHMARK — V4 vs V5\n")
    header = f"{'Mission':<16} {'Arm':<5} {'Time(s)':>9} {'CritPath':>10} {'Tokens':>9} {'Cost':>8} {'Rework':>8} {'Valid%':>8} {'WorkerUtil':>11} {'LeadUtil':>10} {'ApprLat':>9} {'Quality':>8} {'Recov':>6}"
    print(header)
    print("-" * len(header))

    for name, plan in MISSIONS.items():
        v4 = _run_v4_arm(plan)
        v5 = _run_v5_arm(plan)
        rows[name] = {"v4": v4, "v5": v5}

        for arm_name, r in (("V4", v4), ("V5", v5)):
            print(
                f"{name:<16} {arm_name:<5} "
                f"{r.mission_time_s:>9.4f} "
                f"{r.critical_path_duration_s:>10.2f} "
                f"{r.token_usage:>9} "
                f"{r.cost:>8.2f} "
                f"{r.rework_rate*100:>7.0f}% "
                f"{r.validation_success_rate*100:>7.0f}% "
                f"{_fmt(r.worker_utilization * 100 if r.worker_utilization is not None else None, '%'):>11} "
                f"{_fmt(r.lead_utilization * 100 if r.lead_utilization is not None else None, '%'):>10} "
                f"{_fmt(r.approval_latency_s, 's' if r.approval_latency_s is not None else ''):>9} "
                f"{_fmt(r.quality_score):>8} "
                f"{_fmt(r.recovery_attempts):>6}"
            )

    print()
    print("═══ AGGREGATE ═══")
    n = len(rows)
    v4_time = sum(r["v4"].mission_time_s for r in rows.values()) / n
    v5_time = sum(r["v5"].mission_time_s for r in rows.values()) / n
    v5_quality = [r["v5"].quality_score for r in rows.values() if r["v5"].quality_score is not None]
    avg_quality = sum(v5_quality) / len(v5_quality) if v5_quality else 0.0

    print(f"  Avg mission time:        V4 {v4_time:.4f}s   vs   V5 {v5_time:.4f}s")
    print(f"  V4 tracks worker/lead utilization, approval latency, quality, recovery: NO")
    print(f"  V5 tracks worker/lead utilization, approval latency, quality, recovery: YES")
    print(f"  Avg V5 mission quality score: {avg_quality:.0f}/100")
    print(f"  V5 overhead vs V4 (intelligence stack cost): {(v5_time - v4_time):.4f}s "
          f"({(v5_time / v4_time - 1) * 100 if v4_time else 0:.0f}% slower per mission, "
          f"in exchange for the full V5 observability + recovery stack)")

    return rows


if __name__ == "__main__":
    run_benchmark()
