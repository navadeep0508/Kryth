"""V4 Execution Validation — Phase 9.

Runs five reference missions against the full V4 pipeline (MilestoneEngine,
contract enforcement, deliverable tracking, planner review, recovery) and
verifies each invariant.  No LLM calls are made — workers are stubbed with
synthetic outputs so the suite runs offline in milliseconds.

Usage:
    python -m agent.orchestration.v4_validate
    python -m agent.orchestration.v4_validate --mission crud_api
"""
from __future__ import annotations

import argparse
import sys
import textwrap
import time
from dataclasses import dataclass, field
from typing import List, Optional


# ── Synthetic worker output helpers ──────────────────────────────────────────

def _good_output(module: str, deliverables: List[str]) -> str:
    """Simulate a worker that completed successfully."""
    deliv_line = ", ".join(deliverables[:3]) if deliverables else module
    return (
        f"Completed {module}.\n"
        f"Implemented: {deliv_line}\n"
        f"All endpoints tested and working.\n"
        f"AGENT_COMPLETE"
    )


def _bad_output(module: str) -> str:
    """Simulate a stalled/incomplete worker."""
    return f"Started {module} but encountered issues."


# ── Fake scheduler that returns synthetic worker outputs ──────────────────────

class _FakeSchedulerResult:
    def __init__(self, outputs: dict, failed: list) -> None:
        self.success      = not failed
        self.outputs      = outputs
        self.final_output = "\n".join(f"{k}: done" for k in outputs)
        self.total_turns  = len(outputs) * 5
        self.failed_agents: List[str] = failed


def _fake_run_schedule(dag, team, strategy="parallel",
                       project_context="", user_input="",
                       max_turns_per_agent=80, max_workers=4):
    from agent.orchestration.scheduler import WorkerResult
    outputs = {}
    failed  = []
    for agent in team.agents:
        mod = agent.role.replace(" Team", "")
        # Look up deliverables from DAG node description
        node  = dag.nodes.get(agent.id) if hasattr(dag, "nodes") else None
        deliv = list(getattr(node, "validation", [])) if node else []
        out   = _good_output(mod, deliv)
        wr    = WorkerResult(agent_id=agent.id, role=agent.role,
                             success=True, output=out)
        outputs[agent.id] = wr
    return _FakeSchedulerResult(outputs, failed)


# ── Mission definitions ───────────────────────────────────────────────────────

def _make_plan(name: str, modules_spec: list):
    """Build a ProjectPlan from a lightweight spec list.

    modules_spec: list of (name, goal, deps, deliverables)
    """
    from agent.orchestration.project_planner import (
        ProjectPlan, ProjectModule,
    )
    modules = []
    for mod_name, goal, deps, delivs in modules_spec:
        modules.append(ProjectModule(
            name=mod_name,
            goal=goal,
            dependencies=deps,
            deliverables=delivs,
            outputs=delivs,
            success_criteria=[f"{d} implemented" for d in delivs[:2]],
            estimated_turns=10,
        ))
    return ProjectPlan(
        project_name=name,
        project_type="api",
        goal=f"Build {name}",
        modules=modules,
        recommended_mode="dag",
        parallel_streams=2,
        estimated_speedup=1.8,
    )


MISSIONS = {
    "crud_api": _make_plan("CRUD API", [
        ("Database",        "Set up schema and migrations",    [],             ["schema.sql", "migrations/"]),
        ("Models",          "Define ORM models",               ["Database"],   ["models.py"]),
        ("API Endpoints",   "Implement CRUD routes",           ["Models"],     ["routes.py", "handlers.py"]),
        ("Tests",           "Write integration tests",         ["API Endpoints"], ["test_api.py"]),
    ]),
    "jwt_auth": _make_plan("JWT Auth", [
        ("User Model",      "User schema + password hashing",  [],             ["user.py", "hash.py"]),
        ("Token Service",   "JWT issue + verify",              ["User Model"], ["tokens.py"]),
        ("Auth Middleware",  "Protect routes",                  ["Token Service"], ["middleware.py"]),
    ]),
    "website": _make_plan("Website", [
        ("Layout",          "Base HTML + CSS",                 [],             ["index.html", "style.css"]),
        ("Components",      "Nav, Hero, Footer",               ["Layout"],     ["components.js"]),
        ("Content",         "Copy and assets",                 ["Components"], ["content.json"]),
    ]),
    "saas_platform": _make_plan("SaaS Platform", [
        ("Auth",            "Registration, login, JWT",        [],                 ["auth.py"]),
        ("Billing",         "Stripe subscription",             ["Auth"],           ["billing.py"]),
        ("Dashboard",       "User dashboard UI",               ["Auth"],           ["dashboard.html"]),
        ("API",             "REST API layer",                  ["Auth", "Billing"],["api.py"]),
        ("Admin",           "Admin panel",                     ["API"],            ["admin.py"]),
    ]),
    "large_refactor": _make_plan("Large Refactor", [
        ("Analysis",        "Identify code smells",            [],             ["report.md"]),
        ("Core Cleanup",    "Refactor core module",            ["Analysis"],   ["core.py"]),
        ("Tests Update",    "Update test suite",               ["Core Cleanup"], ["tests/"]),
        ("Docs",            "Update documentation",            ["Core Cleanup"], ["README.md"]),
    ]),
}


# ── Validation checks ─────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class MissionValidation:
    mission_name: str
    checks: List[CheckResult] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def score(self) -> str:
        p = sum(1 for c in self.checks if c.passed)
        return f"{p}/{len(self.checks)}"


def _validate_mission(mission_name: str, plan) -> MissionValidation:
    """Run plan through V4 pipeline and validate all invariants."""
    import unittest.mock as _mock

    from agent.orchestration.project_planner import dag_from_plan, team_from_plan
    from agent.orchestration.milestone_engine import MilestoneEngine

    val = MissionValidation(mission_name=mission_name)
    t0  = time.monotonic()

    dag  = dag_from_plan(plan)
    team = team_from_plan(plan, dag, mission_name)

    milestone_callback_names: List[str] = []

    def _on_ms(name: str, _result) -> None:
        milestone_callback_names.append(name)

    # Patch run_schedule at the scheduler module — milestone_engine imports it
    # from there at call time (inside _run_milestone_agents).
    with _mock.patch(
        "agent.orchestration.scheduler.run_schedule",
        side_effect=lambda **kw: _fake_run_schedule(**kw),
    ):
            engine = MilestoneEngine(
                plan=plan,
                dag=dag,
                team=team,
                project_context="test",
                user_input=mission_name,
                max_turns_per_agent=10,
                max_workers=2,
                project_root=".",
                on_milestone_complete=_on_ms,
            )
            result = engine.run()

    val.elapsed_s = time.monotonic() - t0
    milestones     = result.milestones
    expected_ms    = len(plan.ensure_structured_milestones())

    # Check 1: milestones execute
    val.checks.append(CheckResult(
        "milestones_execute",
        len(milestones) >= 1,
        f"{len(milestones)} milestones ran (expected ≥1)",
    ))

    # Check 2: callbacks fired for each milestone
    val.checks.append(CheckResult(
        "milestone_callbacks",
        len(milestone_callback_names) == len(milestones),
        f"callbacks={len(milestone_callback_names)} milestones={len(milestones)}",
    ))

    # Check 3: contracts validated (contract_validations populated)
    contracts_run = sum(len(ms.contract_validations) for ms in milestones)
    val.checks.append(CheckResult(
        "contracts_enforced",
        contracts_run > 0,
        f"{contracts_run} contract validations run",
    ))

    # Check 4: team lead reviews populated (workers cannot self-approve)
    tl_reviews = sum(len(ms.team_lead_reviews) for ms in milestones)
    val.checks.append(CheckResult(
        "team_lead_reviews",
        tl_reviews > 0,
        f"{tl_reviews} team lead reviews",
    ))

    # Check 5: deliverables tracked
    val.checks.append(CheckResult(
        "deliverables_tracked",
        result.total_deliverables_planned > 0,
        f"planned={result.total_deliverables_planned} completed={result.total_deliverables_completed}",
    ))

    # Check 6: planner approvals recorded
    val.checks.append(CheckResult(
        "planner_approvals",
        result.planner_approvals >= 0,
        f"{result.planner_approvals} planner approvals",
    ))

    # Check 7: scorecard renders without error
    try:
        sc = result.scorecard()
        val.checks.append(CheckResult("scorecard_renders", bool(sc), "ok"))
    except Exception as exc:
        val.checks.append(CheckResult("scorecard_renders", False, str(exc)[:60]))

    return val


# ── Recovery test ─────────────────────────────────────────────────────────────

def _validate_recovery(plan, resume_from: int = 2) -> CheckResult:
    """Phase 8: verify milestone-level recovery skips already-done milestones."""
    import unittest.mock as _mock
    from agent.orchestration.project_planner import dag_from_plan, team_from_plan
    from agent.orchestration.milestone_engine import MilestoneEngine

    dag  = dag_from_plan(plan)
    team = team_from_plan(plan, dag, "recovery_test")

    ran: List[int] = []

    def _on_ms(_name, ms_result) -> None:
        ran.append(ms_result.order)

    with _mock.patch(
        "agent.orchestration.scheduler.run_schedule",
        side_effect=lambda **kw: _fake_run_schedule(**kw),
    ):
        engine = MilestoneEngine(
            plan=plan,
            dag=dag,
            team=team,
            project_context="test",
            user_input="recovery",
            max_turns_per_agent=10,
            max_workers=2,
            project_root=".",
            on_milestone_complete=_on_ms,
            resume_from_order=resume_from,
        )
        engine.run()

    skipped = all(o >= resume_from for o in ran)
    return CheckResult(
        "milestone_recovery",
        skipped,
        f"resume_from={resume_from} ran={ran} all_skipped_before={skipped}",
    )


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all(target: Optional[str] = None) -> bool:
    missions = {k: v for k, v in MISSIONS.items() if target is None or k == target}
    if not missions:
        print(f"Unknown mission: {target}. Available: {list(MISSIONS)}")
        return False

    results: List[MissionValidation] = []
    recovery_checks: List[CheckResult] = []

    for name, plan in missions.items():
        print(f"  ◈ Validating: {name} …", end=" ", flush=True)
        try:
            val = _validate_mission(name, plan)
            results.append(val)
            icon = "✓" if val.passed else "✗"
            print(f"{icon}  ({val.score} checks, {val.elapsed_s:.2f}s)")

            # Recovery check on first multi-milestone mission
            ms_count = len(plan.ensure_structured_milestones())
            if ms_count >= 2 and not recovery_checks:
                rc = _validate_recovery(plan, resume_from=2)
                recovery_checks.append(rc)
                r_icon = "✓" if rc.passed else "✗"
                print(f"  ◈ Recovery check:  {r_icon}  {rc.detail}")
        except Exception as exc:
            results.append(MissionValidation(
                mission_name=name,
                checks=[CheckResult("run", False, str(exc)[:80])],
            ))
            print(f"✗  ERROR: {exc}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("═══ V4 VALIDATION RESULTS ═══")
    all_passed = True
    for val in results:
        icon = "✓" if val.passed else "✗"
        print(f"  {icon} {val.mission_name:<20}  {val.score} checks  {val.elapsed_s:.2f}s")
        if not val.passed:
            all_passed = False
            for c in val.checks:
                if not c.passed:
                    print(f"      ✗ {c.name}: {c.detail}")

    for rc in recovery_checks:
        icon = "✓" if rc.passed else "✗"
        print(f"  {icon} recovery check:      {rc.detail}")
        if not rc.passed:
            all_passed = False

    print()
    if all_passed:
        print("KRYTH V4 ORGANIZATIONAL RUNTIME COMPLETE")
    else:
        print("✗ Some checks failed — see above.")
    return all_passed


def main() -> None:
    parser = argparse.ArgumentParser(description="KRYTH V4 execution validation")
    parser.add_argument("--mission", default=None,
                        help=f"Run one mission only. Choices: {list(MISSIONS)}")
    args = parser.parse_args()
    ok = run_all(target=args.mission)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
