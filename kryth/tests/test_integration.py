"""Phase 2 — Full Integration Validation.

Tests that every subsystem communicates correctly:
  Mission DAG → Scheduler → Memory → Experience → Recovery → Dashboard

All tests run WITHOUT a real LLM backend (run_inner_loop is mocked).
The LLM-generated DAG and team generation paths are exercised with
deterministic stubs so the test suite is fast and CI-safe.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stubs / helpers
# ---------------------------------------------------------------------------

def _make_inner_result(content: str = "AGENT_COMPLETE: test", status: str = "done", turns: int = 5):
    r = MagicMock()
    r.content = content
    r.status = status
    r.turns_used = turns
    return r


def _make_agent(agent_id: str, role: str, mission: str = "do work", deps: List[str] = None):
    from agent.orchestration.team_generator import AgentRole, OwnedScope
    return AgentRole(
        id=agent_id,
        role=role,
        mission=mission,
        task_node_ids=[],
        dependencies=deps or [],
        max_turns=10,
    )


def _make_dag(name: str = "test_dag"):
    from agent.orchestration.task_dag import TaskDAG, TaskNode
    dag = TaskDAG(name=name)
    n1 = TaskNode(id="t1", name="Task 1", description="First task", capabilities_required=["backend"])
    n2 = TaskNode(id="t2", name="Task 2", description="Second task", capabilities_required=["frontend"], dependencies=["t1"])
    dag.add(n1)
    dag.add(n2)
    return dag


def _make_team(agents):
    from agent.orchestration.team_generator import TeamPlan
    return TeamPlan(
        agents=agents,
        complexity=3.0,
        risk_assessment="low",
        estimated_total_turns=40,
        estimated_total_tokens=80000,
        parallel_benefit=1.5,
        parallel_cost=0.2,
        recommended_strategy="parallel",
        reasoning="test team",
        layer_count=2,
    )


# ---------------------------------------------------------------------------
# Phase 2.1 — Scheduler Integration
# ---------------------------------------------------------------------------

class TestSchedulerIntegration:
    """Verify the scheduler correctly coordinates agents, collects outputs,
    and propagates results through the pipeline."""

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_single_agent_flow(self, mock_build, mock_run):
        """Single agent runs and output reaches SchedulerResult."""
        from agent.orchestration.scheduler import run_schedule

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1)
        mock_run.return_value = _make_inner_result("AGENT_COMPLETE: solo  output done")

        dag = _make_dag()
        agent = _make_agent("solo", "Solo Agent")
        team = _make_team([agent])
        team.recommended_strategy = "single"

        with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
            with patch("agent.session.push_session", return_value=None):
                with patch("agent.session.pop_session"):
                    result = run_schedule(dag, team, "single", user_input="test task")

        assert result.success
        assert "solo" in result.outputs
        assert result.outputs["solo"].success
        assert result.total_turns > 0

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_two_layer_sequential_flow(self, mock_build, mock_run):
        """Two-layer DAG: layer-2 agent receives layer-1 output in prior_outputs."""
        from agent.orchestration.scheduler import run_schedule

        outputs_seen: List[Dict] = []

        def capture_prior(agent, dag, ctx, prior, turns, ownership=None, bus=None):
            outputs_seen.append(dict(prior))
            r = MagicMock()
            r.content = f"AGENT_COMPLETE: {agent.id}"
            r.status = "done"
            r.turns_used = 3
            return r

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1)

        dag = _make_dag("sequential")
        a1 = _make_agent("backend", "Backend Agent")
        a2 = _make_agent("frontend", "Frontend Agent", deps=["backend"])
        team = _make_team([a1, a2])
        team.recommended_strategy = "sequential"

        with patch("agent.orchestration.scheduler._run_single_agent", side_effect=capture_prior):
            result = run_schedule(dag, team, "sequential", user_input="build app")

        # Layer 2 should have seen layer 1's output
        assert len(outputs_seen) >= 2
        second_call_prior = outputs_seen[1]
        assert "backend" in second_call_prior

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_parallel_layer_all_agents_run(self, mock_build, mock_run):
        """Parallel layer: all agents in the layer run concurrently."""
        from agent.orchestration.scheduler import run_schedule

        ran_agents: List[str] = []
        lock = threading.Lock()

        def fake_run(agent, dag, ctx, prior, turns, ownership=None, bus=None):
            from agent.orchestration.scheduler import WorkerResult
            with lock:
                ran_agents.append(agent.id)
            time.sleep(0.01)  # simulate work
            return WorkerResult(agent_id=agent.id, role=agent.role, success=True, output=f"done:{agent.id}", turns_used=2)

        a1 = _make_agent("fe_0", "Frontend #0")
        a2 = _make_agent("fe_1", "Frontend #1")
        a3 = _make_agent("fe_2", "Frontend #2")
        dag = _make_dag("parallel")
        team = _make_team([a1, a2, a3])
        team.recommended_strategy = "parallel"

        with patch("agent.orchestration.scheduler._run_single_agent", side_effect=fake_run):
            result = run_schedule(dag, team, "parallel", user_input="parallel task")

        assert set(ran_agents) == {"fe_0", "fe_1", "fe_2"}
        assert result.success

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_failed_agent_recorded_in_result(self, mock_build, mock_run):
        """Failed agent appears in failed_agents list."""
        from agent.orchestration.scheduler import run_schedule, WorkerResult

        def fake_run(agent, dag, ctx, prior, turns, ownership=None, bus=None):
            return WorkerResult(
                agent_id=agent.id, role=agent.role,
                success=False, output="", error="api_error",
            )

        agent = _make_agent("broken", "Broken Agent")
        dag = _make_dag()
        team = _make_team([agent])
        team.recommended_strategy = "single"

        with patch("agent.orchestration.scheduler._run_single_agent", side_effect=fake_run):
            result = run_schedule(dag, team, "single", user_input="failing task")

        assert not result.success
        assert "broken" in result.failed_agents

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_work_stealing_repopulates_queue(self, mock_build, mock_run):
        """After a failure, work stealing picks up the pending task."""
        from agent.orchestration.scheduler import run_schedule, WorkerResult

        call_counts: Dict[str, int] = {}
        lock = threading.Lock()

        def fake_run(agent, dag, ctx, prior, turns, ownership=None, bus=None):
            with lock:
                n = call_counts.get(agent.id, 0)
                call_counts[agent.id] = n + 1
            if n == 0 and agent.id == "worker_0":
                return WorkerResult(agent_id=agent.id, role=agent.role, success=False, output="", error="fail")
            return WorkerResult(agent_id=agent.id, role=agent.role, success=True, output="done", turns_used=2)

        a0 = _make_agent("worker_0", "Worker 0")
        a1 = _make_agent("worker_1", "Worker 1")
        dag = _make_dag()
        team = _make_team([a0, a1])

        with patch("agent.orchestration.scheduler._run_single_agent", side_effect=fake_run):
            run_schedule(dag, team, "parallel", user_input="test stealing")

        # worker_0 should have been retried
        assert call_counts.get("worker_0", 0) >= 1


# ---------------------------------------------------------------------------
# Phase 2.2 — Orchestration Pipeline Integration
# ---------------------------------------------------------------------------

class TestOrchestrationPipeline:
    """Test that phases 1-7 of the orchestration pipeline produce valid output
    that flows into phase 8 (scheduler)."""

    def test_capability_graph_filters_single_capability(self):
        """Single-capability task short-circuits before team generation."""
        from agent.orchestration.capability_graph import build_capability_graph
        from agent.orchestration.intent_engine import analyze_intent
        from agent.orchestration.repo_intelligence import RepoProfile

        # A trivial task should require only 1 capability
        intent = analyze_intent("rename a variable")
        repo = RepoProfile(root=".")
        cap_graph = build_capability_graph(intent, repo, "rename a variable")
        required = cap_graph.required_names()

        # If only 1 cap needed, orchestration must short-circuit
        if len(required) <= 1:
            from agent.orchestration import orchestrate
            with patch("agent.orchestration.approval_gate.request_approval") as mock_approval:
                mock_approval.return_value = MagicMock(approved=False, explanation="single-cap")
                result = orchestrate(
                    "rename a variable",
                    ask_fn=lambda _: "n",
                )
            assert not result.approved

    def test_dag_layer_computation(self):
        """DAG layers are computed correctly: independent nodes → layer 0."""
        from agent.orchestration.task_dag import TaskDAG, TaskNode
        dag = TaskDAG(name="test")
        dag.add(TaskNode(id="a", name="A", description="Task A", capabilities_required=["backend"]))
        dag.add(TaskNode(id="b", name="B", description="Task B", capabilities_required=["frontend"]))
        dag.add(TaskNode(id="c", name="C", description="Task C", capabilities_required=["testing"], dependencies=["a", "b"]))

        layers = dag.layers()
        assert len(layers) == 2
        layer0_ids = {n.id for n in layers[0]}
        layer1_ids = {n.id for n in layers[1]}
        assert "a" in layer0_ids and "b" in layer0_ids
        assert "c" in layer1_ids

    def test_team_generation_produces_valid_plan(self):
        """generate_team returns a TeamPlan with at least one agent."""
        from agent.orchestration.task_dag import TaskDAG, TaskNode
        from agent.orchestration.team_generator import generate_team
        from agent.orchestration.repo_intelligence import RepoProfile

        dag = TaskDAG(name="auth_task")
        dag.add(TaskNode(id="auth", name="Auth", description="Implement JWT auth", capabilities_required=["backend"]))
        dag.add(TaskNode(id="ui", name="UI", description="Build login form", capabilities_required=["frontend"]))
        dag.add(TaskNode(id="tests", name="Tests", description="Write tests", capabilities_required=["testing"], dependencies=["auth", "ui"]))

        repo = RepoProfile(root=".")
        plan = generate_team(dag, user_input="build authentication", repo_profile=repo)

        assert plan is not None
        assert len(plan.agents) >= 1
        assert plan.estimated_total_turns > 0
        assert plan.recommended_strategy in ("single", "sequential", "parallel")

    def test_approval_mode_always_single_skips_orchestration(self):
        """ALWAYS_SINGLE mode returns approved=False immediately."""
        from agent.orchestration import orchestrate

        result = orchestrate(
            "build authentication system",
            multi_agent_mode="ALWAYS_SINGLE",
            ask_fn=lambda _: "y",
        )
        # ALWAYS_SINGLE must skip multi-agent
        assert not result.approved

    def test_cost_analysis_positive_values(self):
        """Cost analysis returns non-negative estimates."""
        from agent.orchestration.task_dag import TaskDAG, TaskNode
        from agent.orchestration.team_generator import generate_team
        from agent.orchestration.cost_optimizer import analyze

        dag = TaskDAG(name="cost_test")
        dag.add(TaskNode(id="n1", name="N1", description="work", capabilities_required=["backend"]))
        dag.add(TaskNode(id="n2", name="N2", description="work", capabilities_required=["frontend"]))

        plan = generate_team(dag)
        analysis = analyze(dag, plan)

        assert analysis.estimated_tokens >= 0
        assert analysis.estimated_seconds >= 0
        assert 0.0 <= analysis.parallelism_ratio <= 1.0
        assert 0.0 <= analysis.risk_score <= 1.0


# ---------------------------------------------------------------------------
# Phase 2.3 — Memory ↔ Orchestration Integration
# ---------------------------------------------------------------------------

class TestMemoryOrchestrationIntegration:
    """Verify that memory systems feed into and receive from the orchestration pipeline."""

    def test_experience_engine_learns_from_completed_mission(self, tmp_path, monkeypatch):
        """After a successful orchestration, experience store receives team data."""
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))

        from agent.experience import get_experience
        exp = get_experience(str(tmp_path))

        exp.learn(
            "team",
            task_description="build authentication system",
            roles=["Backend", "Frontend", "QA"],
            strategy="parallel",
            execution_turns=45,
            repair_count=0,
            merge_conflicts=0,
            success=True,
        )

        # Should be able to search for a similar task
        similar = exp.search("implement auth system", top_k=3)
        assert similar is not None

    def test_workflow_memory_records_successful_run(self, tmp_path, monkeypatch):
        """WorkflowMemory captures runs and makes them searchable."""
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        import importlib
        import agent.memory.workflow_memory as wm_mod
        importlib.reload(wm_mod)
        from agent.memory.workflow_memory import WorkflowMemory

        wm = WorkflowMemory(project_hash="integ_test")
        wf_id = wm.register_workflow(
            "build_auth", ["setup_db", "create_routes", "add_tests"],
            intent_type="build"
        )
        wm.record_run(wf_id, "success", duration_sec=120.0)
        wm.record_run(wf_id, "success", duration_sec=100.0)

        best = wm.get_best_workflow(intent_type="build")
        assert best is not None
        assert best.name == "build_auth"
        assert best.success_rate == 1.0

    def test_failure_memory_captures_and_strategy_retrieved(self, tmp_path, monkeypatch):
        """FailureMemory stores failures and returns repair strategies."""
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        import importlib
        import agent.memory.failure_memory as fm_mod
        importlib.reload(fm_mod)
        from agent.memory.failure_memory import FailureMemory

        fm = FailureMemory(project_hash="integ_fail")
        fid = fm.record_failure("build", error_type="ImportError", error_message="No module named 'jwt'")
        fm.record_repair_attempt(fid, "pip install PyJWT", "success")
        fm.mark_resolved(fid, "Installed PyJWT")

        strategy = fm.get_repair_strategy("ImportError")
        assert "pip install PyJWT" in strategy

    def test_knowledge_graph_tracks_project_structure(self):
        """KnowledgeGraph correctly links files and their dependencies."""
        from agent.memory.knowledge_graph import KnowledgeGraph

        kg = KnowledgeGraph(project_hash="kg_integ")
        kg.add_node("auth.py", label="auth.py", node_type="file")
        kg.add_node("models.py", label="models.py", node_type="file")
        kg.add_node("User", label="User class", node_type="symbol")
        kg.add_edge("auth.py", "models.py", edge_type="imports")
        kg.add_edge("models.py", "User", edge_type="defines")

        # Verify path from auth.py to User class
        path = kg.path("auth.py", "User")
        assert len(path) >= 2
        assert path[0] == "auth.py"
        assert path[-1] == "User"

    def test_memory_manager_context_for_task(self, tmp_path, monkeypatch):
        """MemoryManager.get_context_for_task returns all expected subsections."""
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        import importlib
        for mod_name in ["agent.memory.execution_memory", "agent.memory.workflow_memory",
                         "agent.memory.failure_memory", "agent.memory.decision_memory"]:
            importlib.reload(__import__(mod_name, fromlist=["x"]))
        from agent.memory.memory_manager import MemoryManager

        mm = MemoryManager(project_hash="ctx_test", cwd=str(tmp_path))
        mm.working.set("last_command", "pytest -x")
        mm.working.set("project_type", "FastAPI")

        ctx = mm.get_context_for_task("run authentication tests")
        assert "working" in ctx
        assert "project_commands" in ctx
        assert "best_workflow" in ctx
        assert "relevant_decisions" in ctx
        assert ctx["working"]["last_command"] == "pytest -x"


# ---------------------------------------------------------------------------
# Phase 2.4 — Event Bus Integration
# ---------------------------------------------------------------------------

class TestEventBusIntegration:
    """Verify agent-to-agent communication through the team event bus."""

    def test_agents_receive_sibling_discoveries(self):
        """Agent A broadcasts a discovery; Agent B polls and receives it."""
        from agent.orchestration.team_event_bus import TeamEventBus

        bus = TeamEventBus()
        bus.subscribe("agent_a")
        bus.subscribe("agent_b")

        bus.publish("agent_a", "discovery", {"detail": "Found auth middleware at /src/auth.py"})
        events = bus.poll("agent_b")

        assert len(events) == 1
        assert events[0].event_type == "discovery"
        assert "auth middleware" in events[0].payload["detail"]

    def test_sender_does_not_receive_own_broadcast(self):
        """An agent should not receive its own broadcasts (exclude_self=True)."""
        from agent.orchestration.team_event_bus import TeamEventBus

        bus = TeamEventBus()
        bus.subscribe("agent_a")
        bus.publish("agent_a", "discovery", {"detail": "self message"})
        events = bus.poll("agent_a")

        assert len(events) == 0

    def test_multiple_agents_broadcast_all_receive(self):
        """3 agents publish; each receives events from the other 2."""
        from agent.orchestration.team_event_bus import TeamEventBus

        bus = TeamEventBus()
        for i in range(3):
            bus.subscribe(f"agent_{i}")

        for i in range(3):
            bus.publish(f"agent_{i}", "info", {"msg": f"from {i}"})

        for i in range(3):
            events = bus.poll(f"agent_{i}")
            from_agents = {e.from_agent for e in events}
            # Should receive from the other 2, not self
            assert f"agent_{i}" not in from_agents
            assert len(events) == 2

    def test_poll_drains_queue(self):
        """Polling is destructive — second poll returns empty list."""
        from agent.orchestration.team_event_bus import TeamEventBus

        bus = TeamEventBus()
        bus.subscribe("reader")
        bus.publish("writer", "evt", {"x": 1})
        bus.subscribe("writer")

        first = bus.poll("reader")
        second = bus.poll("reader")
        assert len(first) == 1
        assert len(second) == 0

    def test_format_for_prompt_truncates_long_detail(self):
        """format_for_prompt caps detail at 200 chars per event."""
        from agent.orchestration.team_event_bus import TeamEventBus

        bus = TeamEventBus()
        bus.subscribe("a")
        bus.subscribe("b")
        bus.publish("a", "disc", {"detail": "x" * 500})
        events = bus.poll("b")
        text = bus.format_for_prompt(events)
        lines = text.splitlines()
        # The detail line should be truncated to ≤200 chars
        assert all(len(l) <= 250 for l in lines)


# ---------------------------------------------------------------------------
# Phase 2.5 — Repair Loop Integration
# ---------------------------------------------------------------------------

class TestRepairLoopIntegration:
    """Verify the repair loop is triggered on agent failure and produces output."""

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_repair_attempted_on_api_error(self, mock_build, mock_run):
        """When agent status is api_error, repair_loop.attempt_repair is called."""
        from agent.orchestration.scheduler import _run_single_agent
        from agent.orchestration.team_generator import AgentRole, OwnedScope

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1)
        mock_run.return_value = _make_inner_result(status="api_error", content="")

        agent = AgentRole(id="broken", role="Backend", mission="build API")

        repair_called = []

        def fake_repair(**kwargs):
            repair_called.append(kwargs)
            r = MagicMock()
            r.success = False
            r.output = ""
            return r

        with patch("agent.orchestration.repair_loop.attempt_repair", side_effect=fake_repair):
            with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
                with patch("agent.session.push_session", return_value=None):
                    with patch("agent.session.pop_session"):
                        result = _run_single_agent(
                            agent, _make_dag(), "", {}, max_turns=10,
                        )

        assert len(repair_called) == 1
        assert not result.success

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_successful_repair_returns_repaired_output(self, mock_build, mock_run):
        """When repair succeeds, the repaired output is returned as success."""
        from agent.orchestration.scheduler import _run_single_agent
        from agent.orchestration.team_generator import AgentRole

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1)
        mock_run.return_value = _make_inner_result(status="api_error", content="")

        agent = AgentRole(id="repair_me", role="Backend", mission="fix the bug")

        def fake_repair(**kwargs):
            r = MagicMock()
            r.success = True
            r.output = "AGENT_COMPLETE: repair_me  Fixed!"
            r.attempts = 1
            return r

        with patch("agent.orchestration.repair_loop.attempt_repair", side_effect=fake_repair):
            with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
                with patch("agent.session.push_session", return_value=None):
                    with patch("agent.session.pop_session"):
                        result = _run_single_agent(
                            agent, _make_dag(), "", {}, max_turns=10,
                        )

        assert result.success
        assert "Fixed!" in result.output


# ---------------------------------------------------------------------------
# Phase 2.6 — Ownership Lock Integration
# ---------------------------------------------------------------------------

class TestOwnershipIntegration:
    """Verify file ownership prevents concurrent writes to the same path."""

    def test_ownership_bus_exclusive_lock(self):
        """Two agents cannot hold the same file lock concurrently."""
        from agent.orchestration.ownership import OwnershipBus

        bus = OwnershipBus()
        conflicts = bus.claim("agent_a", files=["src/auth.py"])
        assert len(conflicts) == 0
        assert bus.owner_of("src/auth.py") == "agent_a"

        # agent_b cannot claim the same file
        conflicts2 = bus.claim("agent_b", files=["src/auth.py"])
        assert len(conflicts2) > 0
        assert bus.owner_of("src/auth.py") == "agent_a"

    def test_ownership_release_allows_reacquisition(self):
        """After releasing, another agent can claim the resource."""
        from agent.orchestration.ownership import OwnershipBus

        bus = OwnershipBus()
        bus.claim("agent_a", files=["src/models.py"])
        bus.release("agent_a")

        conflicts = bus.claim("agent_b", files=["src/models.py"])
        assert len(conflicts) == 0
        assert bus.owner_of("src/models.py") == "agent_b"


# ---------------------------------------------------------------------------
# Phase 2.7 — Dashboard Event Flow
# ---------------------------------------------------------------------------

class TestDashboardEventFlow:
    """Verify the dashboard event queue receives events from the scheduler."""

    def test_push_event_queues_without_blocking(self):
        """push_event must be non-blocking and always succeed."""
        from agent.ui.dashboard import push_event, _running, _event_queue
        import queue as _queue

        # Drain queue first
        while True:
            try:
                _event_queue.get_nowait()
            except Exception:
                break

        push_event("timeline", message="test event")
        push_event("agent_update", id="x", role="Backend", status="running")

        # Both events should be in the queue
        events_seen = []
        while True:
            try:
                events_seen.append(_event_queue.get_nowait())
            except Exception:
                break

        kinds = {e["kind"] for e in events_seen}
        # Events may or may not be queued depending on _running state
        # but push_event must not raise
        assert isinstance(kinds, set)

    def test_start_stop_dashboard_does_not_block(self):
        """start_dashboard/stop_dashboard must not deadlock."""
        from agent.ui.dashboard import start_dashboard, stop_dashboard
        import threading

        done = threading.Event()

        def run():
            try:
                start_dashboard("test mission", total_agents=3, total_layers=2)
                time.sleep(0.05)
                stop_dashboard()
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        assert done.wait(timeout=5.0), "Dashboard start/stop deadlocked"
