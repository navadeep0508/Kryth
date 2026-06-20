"""Preview → Execution team consistency.

The Mission Execution Preview is the single source of truth: the exact teams
(and their owned tasks) shown in the preview must be the teams spawned. Assert
Preview Teams == Execution Teams and Preview Tasks == Assigned Tasks.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.mission_estimator import estimate
from agent.ui import mission_gate as mg
from agent.orchestration.team_generator import team_from_contract


def test_gate_emits_team_contract():
    est = estimate("build a full-stack web app with frontend backend database and tests")
    out = mg.run_pre_execution_gate(est, interactive=False)
    assert out.team_contract is not None
    assert out.team_contract.team_names == list(out.team_contract.teams.keys())
    assert out.team_contract.teams  # non-empty org


def test_contract_built_from_preview_plan():
    est = estimate("build a full-stack web app with frontend backend database and tests")
    preview = mg.preview_from_estimate(est, mode="dag")
    contract = mg.team_contract_from_preview(preview)
    # the contract's teams are EXACTLY the preview's plan
    assert contract.teams == {k: list(v) for k, v in preview.plan.items()}


def test_preview_teams_equal_execution_teams():
    # Build a contract from the preview, then build the execution TeamPlan from
    # the contract. The agent roles must be exactly the previewed team labels.
    est = estimate("build a full SaaS platform with auth payments docs and tests")
    preview = mg.preview_from_estimate(est, mode="dag")
    contract = mg.team_contract_from_preview(preview)

    team = team_from_contract(contract, user_input="build it")
    exec_team_labels = [a.role for a in team.agents]

    assert set(exec_team_labels) == set(preview.plan.keys())
    assert len(team.agents) == len(preview.plan)        # no inflation, no dropouts


def test_preview_tasks_equal_assigned_tasks():
    est = estimate("create a SaaS landing page with hero, features, pricing, FAQ, testimonials")
    # Force a multi-team preview so there are tasks to own.
    est2 = estimate("build a full-stack web app with frontend backend database and tests")
    preview = mg.preview_from_estimate(est2, mode="dag")
    contract = mg.team_contract_from_preview(preview)
    team = team_from_contract(contract, user_input="x")

    by_role = {a.role: a.task_node_ids for a in team.agents}
    for team_label, tasks in preview.plan.items():
        assert by_role[team_label] == list(tasks), (team_label, by_role[team_label], tasks)


def test_no_substitution_with_legacy_names():
    # The redesign forbids fallback names like "Database Engineer",
    # "Authentication Engineer", "Payment Integration Engineer" unless they were
    # in the preview. Contract teams use the preview's domain labels.
    est = estimate("build a full SaaS platform with auth payments docs and tests")
    preview = mg.preview_from_estimate(est, mode="dag")
    contract = mg.team_contract_from_preview(preview)
    team = team_from_contract(contract, user_input="x")
    roles = {a.role for a in team.agents}
    assert "Database Engineer" not in roles
    assert "Payment Integration Engineer" not in roles
    # roles are exactly the previewed team names
    assert roles == set(preview.plan.keys())


def test_contract_to_dict_roundtrip():
    est = estimate("build a full-stack web app with frontend backend database")
    contract = mg.team_contract_from_preview(mg.preview_from_estimate(est, mode="dag"))
    d = contract.to_dict()
    assert set(d) == {"mode", "teams"}
    assert d["teams"] == contract.teams


def test_team_priority_order_foundational_first():
    # Foundational domains (database/auth/backend) must come before frontend /
    # testing / docs / deploy so dependent work starts on warm outputs and the
    # concurrency cap spends early slots on what unblocks the most.
    contract = type("C", (), {"teams": {
        "Deployment Team": ["deploy"], "Frontend Team": ["hero"],
        "Database Team": ["schema"], "Auth Team": ["login"],
        "Backend Team": ["api"], "Testing Team": ["tests"],
    }})()
    team = team_from_contract(contract, user_input="x")
    order = [a.role for a in team.agents]
    assert order.index("Database Team") < order.index("Backend Team")
    assert order.index("Auth Team") < order.index("Backend Team")
    assert order.index("Backend Team") < order.index("Frontend Team")
    assert order.index("Frontend Team") < order.index("Testing Team")
    assert order.index("Testing Team") < order.index("Deployment Team")


def test_mission_contract_preapproves_worker_writes():
    # With an approved mission contract, worker writes/edits/commands are
    # auto-allowed (no per-file approval prompt that breaks the live dashboard).
    from agent.permissions import check_permission
    from agent.ui.mission_gate import MissionContract

    class _S:
        mission_contract = MissionContract(approved=True, mode="dag")
        remembered_permissions = {}
        # minimal session surface used by check_permission
        def __getattr__(self, k):
            raise AttributeError(k)

    s = _S()
    assert check_permission("write_file", {"path": "src/hero.tsx"}, s) == "allow"
    assert check_permission("edit_file", {"path": "auth.ts"}, s) == "allow"
    assert check_permission("run_command", {"command": "npm install"}, s) == "allow"
    # critical actions are NOT covered by the contract (they fall through to the
    # normal rules instead of being auto-allowed by the contract short-circuit).
    assert s.mission_contract.covers("command", "kubectl apply -f prod.yaml") is False
    assert s.mission_contract.covers("write", "drop database users") is False


def test_concurrent_agent_cap(monkeypatch):
    # The scheduler must cap simultaneously-running agents (default 4) so a
    # 9-agent layer doesn't fire all 9 LLM sessions into the provider at once.
    import agent.orchestration.scheduler as sch
    monkeypatch.delenv("KRYTH_MAX_CONCURRENT_AGENTS", raising=False)
    assert sch._dynamic_worker_count(9, 100000) == 4
    assert sch._dynamic_worker_count(2, 100000) == 2     # never more than team size
    monkeypatch.setenv("KRYTH_MAX_CONCURRENT_AGENTS", "6")
    assert sch._dynamic_worker_count(9, 100000) == 6
    monkeypatch.setenv("KRYTH_MAX_CONCURRENT_AGENTS", "100")
    assert sch._dynamic_worker_count(9, 100000) == 9     # clamped to 16 then team size


def test_orchestrate_uses_contract(monkeypatch):
    # orchestrate() with a team_contract must build the team from it, not from
    # generate_team. We stub the heavy phases and capture the team passed to run.
    import agent.orchestration as orch

    captured = {}

    def fake_execute(**kw):
        captured["team"] = kw["team"]
        from agent.orchestration import OrchestrationResult
        return OrchestrationResult(approved=True, output="done", team=kw["team"])

    # Build a real contract.
    est = estimate("build a full-stack web app with frontend backend database and tests")
    contract = mg.team_contract_from_preview(mg.preview_from_estimate(est, mode="dag"))

    monkeypatch.setattr(orch, "_execute_team", fake_execute)
    # Stub the analysis phases so we reach Phase 5 quickly.
    monkeypatch.setattr(orch, "analyze_repo", lambda *a, **k: orch.RepoProfile(root="."))
    monkeypatch.setattr(orch, "analyze_intent",
                        lambda *a, **k: type("I", (), {"is_compound": True})())

    class _Cap:
        def required_names(self): return ["a", "b", "c"]
    monkeypatch.setattr(orch, "build_capability_graph", lambda *a, **k: _Cap())

    class _DAG:
        nodes = {"n1": 1, "n2": 2}
        def risk_summary(self): return {}
    monkeypatch.setattr(orch, "build_task_dag", lambda *a, **k: _DAG())
    monkeypatch.setattr(orch, "cost_analyze", lambda *a, **k: None)

    res = orch.orchestrate("build a full-stack web app", team_contract=contract,
                           multi_agent_mode="ASK")
    assert res.approved
    roles = {a.role for a in captured["team"].agents}
    assert roles == set(contract.teams.keys())   # exact preview teams executed
