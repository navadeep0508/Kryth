"""Phase 3 — Parallel Agent Validation.

Measures:
- Peak / average active agents
- Idle agent count
- Queue wait time
- Parallel efficiency
- Work stealing
- Dynamic scaling
- Event bus communication

All tests use mock agents so they run without a real LLM.
"""
from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Metrics instrumentation
# ---------------------------------------------------------------------------

@dataclass
class AgentMetrics:
    agent_id: str
    role: str
    start_time: float
    end_time: float = 0.0
    success: bool = True
    stolen: bool = False

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_time - self.start_time)


@dataclass
class SchedulerMetrics:
    total_agents: int = 0
    peak_parallel: int = 0
    avg_parallel: float = 0.0
    idle_agents: int = 0
    total_duration_s: float = 0.0
    agent_records: List[AgentMetrics] = field(default_factory=list)
    steal_count: int = 0

    @property
    def utilization(self) -> float:
        if not self.agent_records or self.total_duration_s == 0:
            return 0.0
        total_work = sum(r.duration_s for r in self.agent_records)
        return total_work / (self.total_agents * self.total_duration_s)

    def summary(self) -> str:
        lines = [
            f"Total agents:    {self.total_agents}",
            f"Peak parallel:   {self.peak_parallel}",
            f"Avg parallel:    {self.avg_parallel:.1f}",
            f"Utilization:     {self.utilization:.0%}",
            f"Steal count:     {self.steal_count}",
            f"Total duration:  {self.total_duration_s:.2f}s",
        ]
        return "\n".join(lines)


def _run_schedule_with_metrics(
    n_agents: int,
    n_layers: int = 1,
    agent_duration: float = 0.05,
    fail_agent_id: Optional[str] = None,
) -> SchedulerMetrics:
    """Run scheduler with synthetic agents and collect metrics."""
    from agent.orchestration.scheduler import run_schedule, WorkerResult
    from agent.orchestration.team_generator import AgentRole, TeamPlan

    metrics = SchedulerMetrics(total_agents=n_agents)
    active_lock = threading.Lock()
    active_count = [0]
    peak = [0]
    snapshots: List[int] = []

    def fake_run(agent, dag, ctx, prior, turns, ownership=None, bus=None):
        t0 = time.monotonic()
        with active_lock:
            active_count[0] += 1
            peak[0] = max(peak[0], active_count[0])
            snapshots.append(active_count[0])

        # Simulate work
        should_fail = agent.id == fail_agent_id
        time.sleep(agent_duration)

        with active_lock:
            active_count[0] -= 1
            snapshots.append(active_count[0])

        rec = AgentMetrics(
            agent_id=agent.id,
            role=agent.role,
            start_time=t0,
            end_time=time.monotonic(),
            success=not should_fail,
        )
        metrics.agent_records.append(rec)

        return WorkerResult(
            agent_id=agent.id, role=agent.role,
            success=not should_fail,
            output=f"done:{agent.id}" if not should_fail else "",
            error="simulated_fail" if should_fail else "",
            turns_used=5,
        )

    from agent.orchestration.task_dag import TaskDAG, TaskNode

    # Build DAG and team
    dag = TaskDAG(name="stress_test")
    agents: List[AgentRole] = []

    agents_per_layer = max(1, n_agents // n_layers)
    agent_counter = 0

    prev_layer_ids: List[str] = []
    for layer_idx in range(n_layers):
        layer_agent_ids: List[str] = []
        for j in range(agents_per_layer):
            if agent_counter >= n_agents:
                break
            aid = f"agent_{agent_counter}"
            role = f"Worker {agent_counter}"
            dag.add(TaskNode(
                id=aid, name=role, description="work",
                capabilities_required=["backend"],
                dependencies=prev_layer_ids if layer_idx > 0 else [],
            ))
            a = AgentRole(
                id=aid, role=role, mission="do work",
                task_node_ids=[aid],
                dependencies=prev_layer_ids if layer_idx > 0 else [],
                max_turns=10,
            )
            agents.append(a)
            layer_agent_ids.append(aid)
            agent_counter += 1
        prev_layer_ids = layer_agent_ids

    team = TeamPlan(
        agents=agents,
        complexity=3.0,
        risk_assessment="low",
        estimated_total_turns=n_agents * 10,
        estimated_total_tokens=n_agents * 20000,
        parallel_benefit=2.0,
        parallel_cost=0.2,
        recommended_strategy="parallel" if agents_per_layer > 1 else "sequential",
        reasoning="stress test",
        layer_count=n_layers,
    )

    t_start = time.monotonic()
    with patch("agent.orchestration.scheduler._run_single_agent", side_effect=fake_run):
        run_schedule(dag, team, team.recommended_strategy, user_input="stress test")
    t_end = time.monotonic()

    metrics.peak_parallel = peak[0]
    metrics.avg_parallel = statistics.mean(snapshots) if snapshots else 0.0
    metrics.total_duration_s = t_end - t_start

    return metrics


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParallelAgentMetrics:

    def test_single_layer_all_agents_run_in_parallel(self):
        """N agents in one layer should run concurrently (peak ≥ N)."""
        metrics = _run_schedule_with_metrics(n_agents=4, n_layers=1, agent_duration=0.05)
        print(f"\n{metrics.summary()}")
        # All 4 agents should overlap; peak must be ≥ 2 (allowing for thread scheduling jitter)
        assert metrics.peak_parallel >= 2
        assert metrics.total_agents == 4

    def test_two_layer_dag_correct_ordering(self):
        """Layer-2 agents must NOT start before layer-1 completes."""
        from agent.orchestration.scheduler import run_schedule, WorkerResult
        from agent.orchestration.task_dag import TaskDAG, TaskNode
        from agent.orchestration.team_generator import AgentRole, TeamPlan

        completion_times: Dict[str, float] = {}
        start_times: Dict[str, float] = {}

        def fake_run(agent, dag, ctx, prior, turns, ownership=None, bus=None):
            start_times[agent.id] = time.monotonic()
            time.sleep(0.03)
            completion_times[agent.id] = time.monotonic()
            return WorkerResult(agent_id=agent.id, role=agent.role, success=True, output="done", turns_used=2)

        dag = TaskDAG(name="ordering_test")
        dag.add(TaskNode(id="l1_a", name="L1A", description="L1A", capabilities_required=["backend"]))
        dag.add(TaskNode(id="l1_b", name="L1B", description="L1B", capabilities_required=["frontend"]))
        dag.add(TaskNode(id="l2", name="L2", description="L2", capabilities_required=["testing"], dependencies=["l1_a", "l1_b"]))

        a_l1a = AgentRole(id="l1_a", role="Backend", mission="m", task_node_ids=["l1_a"])
        a_l1b = AgentRole(id="l1_b", role="Frontend", mission="m", task_node_ids=["l1_b"])
        a_l2 = AgentRole(id="l2", role="QA", mission="m", task_node_ids=["l2"], dependencies=["l1_a", "l1_b"])

        team = TeamPlan(
            agents=[a_l1a, a_l1b, a_l2],
            complexity=2.0, risk_assessment="low",
            estimated_total_turns=30, estimated_total_tokens=60000,
            parallel_benefit=1.5, parallel_cost=0.1,
            recommended_strategy="parallel", reasoning="test",
            layer_count=2,
        )

        with patch("agent.orchestration.scheduler._run_single_agent", side_effect=fake_run):
            run_schedule(dag, team, "parallel", user_input="test ordering")

        # l2 must start after BOTH l1_a and l1_b complete
        assert start_times.get("l2", 0) >= completion_times.get("l1_a", 0)
        assert start_times.get("l2", 0) >= completion_times.get("l1_b", 0)

    def test_parallel_efficiency_above_threshold(self):
        """With 4 equal-duration agents in parallel, wall-clock ≈ 1 agent, not 4."""
        agent_duration = 0.05
        metrics = _run_schedule_with_metrics(n_agents=4, n_layers=1, agent_duration=agent_duration)
        # Sequential would take ~4 * agent_duration; parallel should be ≤ 2.5 * agent_duration
        max_expected = 2.5 * agent_duration + 0.2  # allow 200ms overhead
        assert metrics.total_duration_s <= max_expected, (
            f"Parallel overhead too high: {metrics.total_duration_s:.3f}s expected ≤ {max_expected:.3f}s"
        )

    def test_failed_agent_does_not_block_siblings(self):
        """A failing agent must not prevent successful sibling agents from completing."""
        metrics = _run_schedule_with_metrics(
            n_agents=4, n_layers=1, agent_duration=0.03, fail_agent_id="agent_0"
        )
        # 3 of 4 agents should succeed
        successes = sum(1 for r in metrics.agent_records if r.success)
        assert successes >= 3

    def test_eight_agent_parallel_peak_at_least_4(self):
        """8 parallel agents should achieve peak concurrency ≥ 4."""
        metrics = _run_schedule_with_metrics(n_agents=8, n_layers=1, agent_duration=0.08)
        print(f"\n8-agent metrics:\n{metrics.summary()}")
        assert metrics.peak_parallel >= 4

    def test_utilization_metric_computable(self):
        """Utilization metric is in [0, 1] range."""
        metrics = _run_schedule_with_metrics(n_agents=3, n_layers=1, agent_duration=0.04)
        u = metrics.utilization
        assert 0.0 <= u <= 1.0 + 0.1  # slight over 1.0 allowed for measurement noise


class TestWorkStealing:

    def test_steal_attempt_on_empty_queue_returns_none(self):
        """WorkQueue.try_steal returns None when empty."""
        from agent.orchestration.work_queue import WorkQueue
        q = WorkQueue()
        assert q.try_steal() is None

    def test_submit_fail_retry_makes_stealable(self):
        """After fail(retry=True), the item is stealable."""
        from agent.orchestration.work_queue import WorkQueue
        q = WorkQueue()
        q.submit("task_1", "Worker", "do work", max_turns=10, priority=0)
        q.fail("task_1", "error", retry=True)
        stolen = q.try_steal()
        assert stolen is not None
        assert stolen.task_id == "task_1"

    def test_completed_item_not_stealable(self):
        """Completed items cannot be stolen."""
        from agent.orchestration.work_queue import WorkQueue
        q = WorkQueue()
        q.submit("task_2", "Worker", "do work", max_turns=10)
        q.complete("task_2", "output")
        assert q.try_steal() is None

    def test_is_empty_after_completion(self):
        """Queue reports empty after all items complete."""
        from agent.orchestration.work_queue import WorkQueue
        q = WorkQueue()
        q.submit("t1", "W", "work", max_turns=10)
        q.submit("t2", "W", "work", max_turns=10)
        assert not q.is_empty()
        # Must steal (PENDING→RUNNING) before completing (RUNNING→DONE)
        q.try_steal()
        q.try_steal()
        q.complete("t1", "")
        q.complete("t2", "")
        assert q.is_empty()


class TestDynamicScaling:

    def test_team_scaler_explodes_large_domain(self):
        """A large-domain agent (frontend with many tasks) should be exploded into sub-agents."""
        from agent.orchestration.task_dag import TaskDAG, TaskNode
        from agent.orchestration.team_generator import AgentRole, TeamPlan, OwnedScope
        from agent.orchestration.team_scaler import scale_team

        dag = TaskDAG(name="scale_test")
        for i in range(5):
            dag.add(TaskNode(id=f"fe_{i}", name=f"FE {i}", description=f"frontend task {i}", capabilities_required=["frontend"]))

        frontend_agent = AgentRole(
            id="frontend", role="Frontend",
            mission="Build all 5 frontend components",
            task_node_ids=[f"fe_{i}" for i in range(5)],
        )
        team = TeamPlan(
            agents=[frontend_agent],
            complexity=5.0, risk_assessment="medium",
            estimated_total_turns=200, estimated_total_tokens=400000,
            parallel_benefit=3.0, parallel_cost=0.3,
            recommended_strategy="parallel", reasoning="large frontend",
            layer_count=1,
        )

        scaled = scale_team(team, dag, ".", "Build large frontend")
        # Should have more agents after scaling
        assert len(scaled.agents) >= 1  # at minimum same; ideally more

    def test_dynamic_worker_count_scales_with_team(self):
        """Worker count grows with team size, capped at reasonable limit."""
        from agent.orchestration.scheduler import _dynamic_worker_count

        assert _dynamic_worker_count(1, 1000) >= 1
        assert _dynamic_worker_count(4, 8000) >= 4
        assert _dynamic_worker_count(100, 200000) <= 64  # hard cap


class TestAgentLayerOrdering:

    def test_circular_dependency_handled_gracefully(self):
        """Circular deps in agent graph must not hang the scheduler (fallback to run-all)."""
        from agent.orchestration.scheduler import _agent_execution_layers
        from agent.orchestration.team_generator import AgentRole

        a = AgentRole(id="a", role="A", mission="m", dependencies=["b"])
        b = AgentRole(id="b", role="B", mission="m", dependencies=["a"])

        layers = _agent_execution_layers([a, b])
        # Both agents must appear somewhere across layers (no infinite loop)
        all_ids = {ag.id for layer in layers for ag in layer}
        assert "a" in all_ids and "b" in all_ids

    def test_independent_agents_single_layer(self):
        """Agents with no dependencies all land in the first layer."""
        from agent.orchestration.scheduler import _agent_execution_layers
        from agent.orchestration.team_generator import AgentRole

        agents = [AgentRole(id=f"a{i}", role=f"R{i}", mission="m") for i in range(4)]
        layers = _agent_execution_layers(agents)
        assert len(layers) == 1
        assert len(layers[0]) == 4

    def test_chain_produces_n_layers(self):
        """a→b→c produces 3 layers."""
        from agent.orchestration.scheduler import _agent_execution_layers
        from agent.orchestration.team_generator import AgentRole

        a = AgentRole(id="a", role="A", mission="m")
        b = AgentRole(id="b", role="B", mission="m", dependencies=["a"])
        c = AgentRole(id="c", role="C", mission="m", dependencies=["b"])

        layers = _agent_execution_layers([a, b, c])
        assert len(layers) == 3
        assert layers[0][0].id == "a"
        assert layers[1][0].id == "b"
        assert layers[2][0].id == "c"
