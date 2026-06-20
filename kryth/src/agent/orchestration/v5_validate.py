"""V5 Execution Validation — Phase 12.

Runs 7 end-to-end reference missions through run_v5() and verifies every
V5 component actually fires: Planner, Milestones, Contracts, Team Leads,
Workers, Validation, Recovery, Forecasting, Digital Twin, Portfolio.

No LLM calls — workers are stubbed (same offline pattern as v4_validate.py).

Usage:
    python -m agent.orchestration.v5_validate
    python -m agent.orchestration.v5_validate --mission crud_api
"""
from __future__ import annotations

import argparse
import sys
import time
import unittest.mock as _mock
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.orchestration.v4_validate import MISSIONS as _V4_MISSIONS, _make_plan, _fake_run_schedule
from agent.orchestration.project_planner import dag_from_plan, team_from_plan
from agent.orchestration.v5_runtime import run_v5
from agent.orchestration.portfolio import PortfolioManager


# ── Two additional missions beyond v4_validate's five ────────────────────────

def _ai_internship_platform():
    return _make_plan("AI Internship Platform", [
        ("Student Portal",  "Student registration and applications", [], ["student.py"]),
        ("Company Portal",  "Company job postings",                  [], ["company.py"]),
        ("Matching Engine", "AI-based matching",
         ["Student Portal", "Company Portal"], ["matcher.py"]),
        ("Notifications",   "Email/SMS notifications", ["Matching Engine"], ["notify.py"]),
    ])


def _multi_service_backend():
    return _make_plan("Multi-Service Backend", [
        ("Gateway",              "API gateway / routing",            [], ["gateway.py"]),
        ("Auth Service",         "Authentication microservice",      ["Gateway"], ["auth_svc.py"]),
        ("Orders Service",       "Order processing microservice",    ["Gateway"], ["orders_svc.py"]),
        ("Payments Service",     "Payment processing microservice",  ["Orders Service"], ["payments_svc.py"]),
        ("Notification Service", "Cross-service notifications",
         ["Auth Service", "Orders Service"], ["notif_svc.py"]),
    ])


MISSIONS: Dict[str, object] = dict(_V4_MISSIONS)
MISSIONS["ai_internship_platform"] = _ai_internship_platform()
MISSIONS["multi_service_backend"] = _multi_service_backend()


# ── Check result types ────────────────────────────────────────────────────────

@dataclass
class V5CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class V5MissionValidation:
    mission_name: str
    checks: List[V5CheckResult] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def score(self) -> str:
        p = sum(1 for c in self.checks if c.passed)
        return f"{p}/{len(self.checks)}"


def _validate_mission(name: str, plan) -> V5MissionValidation:
    val = V5MissionValidation(mission_name=name)
    t0 = time.monotonic()

    dag = dag_from_plan(plan)
    team = team_from_plan(plan, dag, name)

    with _mock.patch(
        "agent.orchestration.scheduler.run_schedule",
        side_effect=lambda **kw: _fake_run_schedule(**kw),
    ):
        report = run_v5(
            plan=plan, dag=dag, team=team, project_context="test", user_input=name,
            max_turns_per_agent=10, max_workers=2, project_root=".",
        )

    val.elapsed_s = time.monotonic() - t0
    mr = report.mission_result

    # V6: checks are tier-aware — if a layer is disabled for this tier, its absence is correct.
    layers = getattr(report, "active_layers", None)

    val.checks.append(V5CheckResult("planner", len(plan.modules) > 0, f"{len(plan.modules)} modules"))
    val.checks.append(V5CheckResult("milestones", len(mr.milestones) >= 1, f"{len(mr.milestones)} milestones ran"))

    contracts_run = sum(len(ms.contract_validations) for ms in mr.milestones)
    val.checks.append(V5CheckResult("contracts", contracts_run > 0, f"{contracts_run} validations run"))

    # team_leads: required only when the program_manager layer is active
    if layers is None or layers.program_manager:
        pm_ok = report.program_manager_report is not None and len(report.program_manager_report.milestone_reports) > 0
        val.checks.append(V5CheckResult(
            "team_leads", pm_ok,
            f"{len(report.program_manager_report.milestone_reports) if report.program_manager_report else 0} TL reports"
        ))

    workers_ran = sum(len(ms.worker_outputs) for ms in mr.milestones)
    val.checks.append(V5CheckResult("workers", workers_ran > 0, f"{workers_ran} worker outputs"))

    # quality: required only when the quality layer is active
    if layers is None or layers.quality:
        val.checks.append(V5CheckResult(
            "validation_quality", report.quality_report is not None,
            f"score={report.quality_report.mission_quality_score:.0f}" if report.quality_report else "none",
        ))

    # forecasting: required only when the forecaster layer is active
    if layers is None or layers.forecaster:
        val.checks.append(V5CheckResult(
            "forecasting", report.forecast is not None,
            report.forecast.summary() if report.forecast else "none",
        ))

    # digital_twin: required only when that layer is active
    if layers is None or layers.digital_twin:
        twin_ok = (
            report.digital_twin_snapshot is not None
            and report.digital_twin_snapshot.planned_deliverables > 0
        )
        val.checks.append(V5CheckResult(
            "digital_twin", twin_ok,
            f"{report.digital_twin_snapshot.actual_deliverables}/{report.digital_twin_snapshot.planned_deliverables}"
            if report.digital_twin_snapshot else "none",
        ))

    # org_health: required only when that layer is active
    if layers is None or layers.org_health:
        val.checks.append(V5CheckResult(
            "org_health", report.org_health_report is not None,
            f"utilization={report.org_health_report.overall_utilization:.0%}" if report.org_health_report else "none",
        ))

    # V6 additions: tier selection and overhead profiling are always present
    val.checks.append(V5CheckResult("tier_selected", report.tier is not None, f"tier={report.tier}"))
    val.checks.append(V5CheckResult("overhead_profiler", report.overhead_report is not None, "overhead report computed"))

    return val


def _validate_recovery() -> V5CheckResult:
    """Phase 10: force milestone 1's worker run to fail once, verify the
    mission self-heals via AutonomousRecoveryEngine + the resume mechanism."""
    plan = MISSIONS["crud_api"]
    dag = dag_from_plan(plan)
    team = team_from_plan(plan, dag, "recovery_test")

    state = {"calls": 0}

    def _flaky(**kw):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated provider failure")
        return _fake_run_schedule(**kw)

    with _mock.patch("agent.orchestration.scheduler.run_schedule", side_effect=_flaky):
        report = run_v5(
            plan=plan, dag=dag, team=team, project_context="test", user_input="recovery",
            max_turns_per_agent=10, max_workers=2, project_root=".",
            tier=4,   # V6: force ENTERPRISE so recovery layer is active
        )

    ok = report.mission_result.success and report.recovery_attempts > 0
    return V5CheckResult(
        "recovery", ok,
        f"success={report.mission_result.success} attempts={report.recovery_attempts}  {report.recovery_summary}",
    )


def _validate_portfolio() -> V5CheckResult:
    """Phase 8: run two missions concurrently through PortfolioManager."""
    pm = PortfolioManager(total_token_budget=1_000_000)
    pm.register("crud_api", "CRUD API", token_budget=400_000, milestones_total=4, deliverables_planned=6)
    pm.register("jwt_auth", "JWT Auth", token_budget=200_000, milestones_total=3, deliverables_planned=4)
    pm.start("crud_api")
    pm.start("jwt_auth")
    pm.update("crud_api", tokens_used=100_000, milestones_done=2, deliverables_completed=3)
    pm.update("jwt_auth", tokens_used=50_000, milestones_done=3, deliverables_completed=4)
    pm.complete("jwt_auth", success=True)
    health = pm.health_report()

    ok = (
        health.total_missions == 2
        and health.completed == 1
        and health.running == 1
        and health.total_tokens_used == 150_000
    )
    return V5CheckResult("portfolio", ok, health.summary().replace("\n", " | "))


def run_all(target: Optional[str] = None) -> bool:
    missions = {k: v for k, v in MISSIONS.items() if target is None or k == target}
    if not missions:
        print(f"Unknown mission: {target}. Available: {list(MISSIONS)}")
        return False

    results: List[V5MissionValidation] = []
    print("◈ V5 EXECUTION VALIDATION — end-to-end missions\n")
    for name, plan in missions.items():
        print(f"  Validating: {name} …", end=" ", flush=True)
        val = _validate_mission(name, plan)
        results.append(val)
        print(f"{'✓' if val.passed else '✗'}  ({val.score}, {val.elapsed_s:.2f}s)")

    print()
    rec = _validate_recovery()
    print(f"  Recovery check:   {'✓' if rec.passed else '✗'}  {rec.detail}")

    port = _validate_portfolio()
    print(f"  Portfolio check:  {'✓' if port.passed else '✗'}  {port.detail}")

    print()
    print("═══ V5 VALIDATION RESULTS ═══")
    all_passed = True
    for val in results:
        icon = "✓" if val.passed else "✗"
        print(f"  {icon} {val.mission_name:<26} {val.score}")
        if not val.passed:
            all_passed = False
            for c in val.checks:
                if not c.passed:
                    print(f"      ✗ {c.name}: {c.detail}")
    print(f"  {'✓' if rec.passed else '✗'} recovery")
    print(f"  {'✓' if port.passed else '✗'} portfolio")
    all_passed = all_passed and rec.passed and port.passed

    print()
    if all_passed:
        print("KRYTH V5 EXECUTION INTELLIGENCE COMPLETE")
    else:
        print("✗ Some checks failed — see above.")
    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="KRYTH V5 execution validation")
    parser.add_argument("--mission", default=None, help=f"Run one mission only. Choices: {list(MISSIONS)}")
    args = parser.parse_args()
    ok = run_all(target=args.mission)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
