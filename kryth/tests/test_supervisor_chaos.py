"""Phase 6 — Supervisor / Chaos Validation.

Force failures and verify the recovery mechanisms work correctly:
- Repair loop fires on agent failure
- Scheduler retries failed agents
- Ownership locks are released even on exception
- Context variables are isolated between parallel agents
- Work queue handles failures gracefully
"""
from __future__ import annotations

import threading
import time
from typing import List
from unittest.mock import MagicMock, patch

import pytest


def _make_agent(agent_id: str, role: str = "Worker", deps: List[str] = None):
    from agent.orchestration.team_generator import AgentRole
    return AgentRole(
        id=agent_id, role=role, mission="do work",
        task_node_ids=[], dependencies=deps or [], max_turns=10,
    )


def _make_dag():
    from agent.orchestration.task_dag import TaskDAG, TaskNode
    dag = TaskDAG(name="chaos")
    dag.add(TaskNode(id="t1", name="T1", description="task", capabilities_required=["backend"]))
    return dag


# ---------------------------------------------------------------------------
# Kill-agent scenarios
# ---------------------------------------------------------------------------

class TestAgentFailureRecovery:

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_interrupted_agent_triggers_repair(self, mock_build, mock_run):
        """Status=interrupted fires the repair loop."""
        from agent.orchestration.scheduler import _run_single_agent

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1)
        mock_run.return_value = MagicMock(status="interrupted", content="", turns_used=2)

        repair_calls = []

        def fake_repair(**kwargs):
            repair_calls.append(True)
            r = MagicMock(); r.success = False; r.output = ""
            return r

        with patch("agent.orchestration.repair_loop.attempt_repair", side_effect=fake_repair):
            with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
                with patch("agent.session.push_session", return_value=None):
                    with patch("agent.session.pop_session"):
                        result = _run_single_agent(_make_agent("x"), _make_dag(), "", {}, 10)

        assert len(repair_calls) == 1
        assert not result.success

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_exception_in_agent_returns_failure_result(self, mock_build, mock_run):
        """Raw exception from run_inner_loop produces a failed WorkerResult, not a crash."""
        from agent.orchestration.scheduler import _run_single_agent

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1)
        mock_run.side_effect = RuntimeError("LLM API connection timeout")

        with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
            with patch("agent.session.push_session", return_value=None):
                with patch("agent.session.pop_session"):
                    result = _run_single_agent(_make_agent("exploder"), _make_dag(), "", {}, 10)

        assert not result.success
        # Either the raw exception message or the friendly provider-error label
        assert (
            "LLM API connection timeout" in result.error
            or "timeout" in result.error.lower()
            or "provider" in result.error.lower()
        )

    @patch("agent.agent_loop.run_inner_loop")
    @patch("agent.tools._subagent._build_nested")
    def test_ownership_lock_released_on_exception(self, mock_build, mock_run):
        """Ownership lock must be released even if agent raises."""
        from agent.orchestration.scheduler import _run_single_agent
        from agent.orchestration.team_generator import AgentRole, OwnedScope

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1)
        mock_run.side_effect = RuntimeError("crash")

        released = []

        class FakeOwnership:
            def acquire_all(self, keys): pass
            def release_all(self, keys): released.extend(keys)

        agent = AgentRole(
            id="locker", role="Locker", mission="m",
            owns=OwnedScope(files=["src/auth.py"], directories=["src/"]),
        )

        with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
            with patch("agent.session.push_session", return_value=None):
                with patch("agent.session.pop_session"):
                    _run_single_agent(agent, _make_dag(), "", {}, 10, ownership_mgr=FakeOwnership())

        assert "FILE:src/auth.py" in released
        assert "DIR:src/" in released

    def test_work_queue_retry_logic(self):
        """Failing an item with retry=True makes it available for stealing."""
        from agent.orchestration.work_queue import WorkQueue
        q = WorkQueue()
        q.submit("t1", "Worker", "prompt", max_turns=10)
        q.fail("t1", "crash", retry=True)
        stolen = q.try_steal()
        assert stolen is not None and stolen.task_id == "t1"

    def test_work_queue_no_retry_does_not_requeue(self):
        """fail(retry=False) must not make the item stealable."""
        from agent.orchestration.work_queue import WorkQueue
        q = WorkQueue()
        q.submit("t1", "Worker", "prompt", max_turns=10)
        q.fail("t1", "hard failure", retry=False)
        assert q.try_steal() is None


# ---------------------------------------------------------------------------
# Context isolation between parallel agents
# ---------------------------------------------------------------------------

class TestContextIsolation:

    def test_parallel_agents_have_isolated_sessions(self):
        """Each parallel agent must see its own session, not a sibling's."""
        from agent.session import push_session, pop_session, get_session, Session

        modes_seen: List[str] = []
        lock = threading.Lock()
        barrier = threading.Barrier(3)

        def worker(session_id: int):
            s = Session()
            # Use mode as the identity marker; push_session owns depth so
            # we must not rely on it for isolation checks.
            s.mode = f"worker_{session_id}"
            token = push_session(s)
            try:
                barrier.wait(timeout=2.0)  # all three at the same time
                with lock:
                    modes_seen.append(get_session().mode)
            finally:
                pop_session(token)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        # Each thread should have seen its own session, not a sibling's
        assert sorted(modes_seen) == ["worker_0", "worker_1", "worker_2"]

    def test_context_var_copy_prevents_sharing(self):
        """contextvars.copy_context() gives each thread its own ContextVar slot."""
        import contextvars

        _var: contextvars.ContextVar[int] = contextvars.ContextVar("test_var", default=-1)
        results: List[int] = []
        lock = threading.Lock()

        def worker(value: int):
            ctx = contextvars.copy_context()

            def _inner():
                _var.set(value)
                time.sleep(0.02)
                with lock:
                    results.append(_var.get())

            ctx.run(_inner)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        # Each thread should have seen its own value
        assert sorted(results) == [0, 1, 2, 3]


# ---------------------------------------------------------------------------
# Build / dependency failures
# ---------------------------------------------------------------------------

class TestBuildFailureRecovery:

    def test_repair_loop_max_retries_exhausted_returns_failure(self, tmp_path):
        """When all repair attempts fail, RepairResult is failure with descriptive error."""
        from agent.orchestration.repair_loop import RepairResult

        call_count = [0]

        def fake_run_inner(session, turns, verbose_usage=False):
            call_count[0] += 1
            r = MagicMock()
            r.status = "api_error"
            r.content = ""
            r.turns_used = 2
            return r

        with patch("agent.agent_loop.run_inner_loop", side_effect=fake_run_inner):
            with patch("agent.tools._subagent._build_nested", return_value=MagicMock(messages=[], system_prompt="", depth=1)):
                with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
                    with patch("agent.session.push_session", return_value=None):
                        with patch("agent.session.pop_session"):
                            from agent.orchestration.repair_loop import attempt_repair
                            result = attempt_repair(
                                agent_id="broken",
                                role="Backend",
                                mission="fix stuff",
                                original_error="build failed: undefined symbol",
                                project_context="",
                                max_turns=5,
                            )

        assert isinstance(result, RepairResult)
        assert not result.success
        assert result.error is not None

    def test_scheduler_marks_all_failed_agents(self):
        """If all agents fail, SchedulerResult.success=False and all IDs in failed_agents."""
        from agent.orchestration.scheduler import run_schedule, WorkerResult
        from agent.orchestration.team_generator import TeamPlan

        def always_fail(agent, dag, ctx, prior, turns, ownership=None, bus=None):
            return WorkerResult(agent_id=agent.id, role=agent.role, success=False, output="", error="forced fail")

        from agent.orchestration.task_dag import TaskDAG, TaskNode
        dag = TaskDAG(name="all_fail")
        for i in range(3):
            dag.add(TaskNode(id=f"n{i}", name=f"N{i}", description="task", capabilities_required=["backend"]))

        from agent.orchestration.team_generator import AgentRole
        agents = [AgentRole(id=f"a{i}", role=f"R{i}", mission="m", task_node_ids=[f"n{i}"]) for i in range(3)]
        team = TeamPlan(
            agents=agents, complexity=2.0, risk_assessment="high",
            estimated_total_turns=30, estimated_total_tokens=60000,
            parallel_benefit=1.0, parallel_cost=0.3,
            recommended_strategy="parallel", reasoning="fail test",
            layer_count=1,
        )

        with patch("agent.orchestration.scheduler._run_single_agent", side_effect=always_fail):
            result = run_schedule(dag, team, "parallel", user_input="fail all")

        assert not result.success
        assert len(result.failed_agents) == 3


# ---------------------------------------------------------------------------
# Self-healing confidence
# ---------------------------------------------------------------------------

class TestSelfHealing:

    def test_repair_succeeds_on_second_attempt(self):
        """Simulate: first LLM call fails (api_error), second succeeds (done with sentinel)."""
        attempt = [0]

        def fake_run_inner(session, turns, verbose_usage=False):
            attempt[0] += 1
            r = MagicMock()
            if attempt[0] == 1:
                r.status = "api_error"
                r.content = ""
            else:
                r.status = "done"
                r.content = "REPAIR_COMPLETE: broken\nFixed the import error."
            r.turns_used = 3
            return r

        with patch("agent.agent_loop.run_inner_loop", side_effect=fake_run_inner):
            with patch("agent.tools._subagent._build_nested", return_value=MagicMock(messages=[], system_prompt="", depth=1)):
                with patch("agent.session.get_session", return_value=MagicMock(depth=0)):
                    with patch("agent.session.push_session", return_value=None):
                        with patch("agent.session.pop_session"):
                            from agent.orchestration.repair_loop import attempt_repair
                            result = attempt_repair(
                                agent_id="broken",
                                role="Backend",
                                mission="fix import error",
                                original_error="ImportError: No module named 'jwt'",
                                project_context="",
                                max_turns=10,
                            )

        assert result.success
        assert "Fixed" in result.output

    def test_repair_result_schema(self):
        """RepairResult has expected fields regardless of outcome."""
        from agent.orchestration.repair_loop import RepairResult

        r_success = RepairResult(success=True, output="done", attempts=1, error="")
        assert r_success.success
        assert r_success.output == "done"

        r_fail = RepairResult(success=False, output="", attempts=3, error="all retries failed")
        assert not r_fail.success
        assert r_fail.attempts == 3
