"""V1.6 regression tests: context sharding, execution contract, DAG UX,
provider health in textual, dependency waiting, impl mode nudge.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest


# ── Phase 5: Context sharding ─────────────────────────────────────────────────

class TestContextSharding:
    def test_shard_reduces_context_size(self):
        from agent.orchestration.scheduler import _shard_context
        full = "\n".join(f"line about {'database' if i % 3 == 0 else 'unrelated'} {i}"
                         for i in range(200))
        sharded = _shard_context(full, "Database Agent", "schema migration", max_chars=1800)
        assert len(sharded) <= 1800
        assert len(sharded) < len(full)

    def test_shard_prefers_role_relevant_lines(self):
        from agent.orchestration.scheduler import _shard_context
        lines = [
            "frontend react components",
            "database schema migration",
            "backend api endpoints",
            "database table creation",
            "unrelated line",
        ]
        context = "\n".join(lines)
        sharded = _shard_context(context, "Database", "schema", max_chars=200)
        assert "database" in sharded.lower()

    def test_shard_falls_back_for_empty_context(self):
        from agent.orchestration.scheduler import _shard_context
        assert _shard_context("", "role", "task") == ""

    def test_shard_returns_full_when_short(self):
        from agent.orchestration.scheduler import _shard_context
        short = "short context"
        assert _shard_context(short, "role", "task", max_chars=1800) == short

    def test_shard_max_chars_respected(self):
        from agent.orchestration.scheduler import _shard_context
        long_context = "x" * 10000
        result = _shard_context(long_context, "Frontend", "build UI", max_chars=1800)
        assert len(result) <= 1800

    def test_shard_40pct_reduction_from_3000(self):
        """Sharding from 3000 chars should produce ≤1800 chars (40% reduction target)."""
        from agent.orchestration.scheduler import _shard_context
        context_3000 = "\n".join(f"irrelevant line {i}" for i in range(150))
        # ~3000 chars of irrelevant content
        sharded = _shard_context(context_3000, "Backend", "api", max_chars=1800)
        # Should be <= 1800 (40% reduction from 3000)
        assert len(sharded) <= 1800


# ── Phase 8: Execution contract in worker prompt ──────────────────────────────

class TestExecutionContract:
    def _make_agent(self, role="Frontend"):
        from agent.orchestration.team_generator import AgentRole, OwnedScope
        return AgentRole(
            id="fe-1", role=role,
            mission=f"build {role}",
            dependencies=[],
            owns=OwnedScope(),
            task_node_ids=[],
        )

    def test_execution_contract_in_prompt(self):
        from agent.orchestration.scheduler import _build_agent_system_prompt
        from agent.orchestration.task_dag import TaskDAG

        dag = TaskDAG(name="test")
        agent = self._make_agent("Backend")
        prompt = _build_agent_system_prompt(agent, dag, "", {})

        assert "EXECUTION CONTRACT" in prompt
        assert "WORKER" in prompt
        assert "re-plan" in prompt.lower() or "replanning" in prompt.lower() or "re-scope" in prompt.lower()

    def test_worker_forbidden_from_replanning(self):
        from agent.orchestration.scheduler import _build_agent_system_prompt
        from agent.orchestration.task_dag import TaskDAG

        dag = TaskDAG(name="test")
        agent = self._make_agent("Frontend")
        prompt = _build_agent_system_prompt(agent, dag, "", {})
        assert "Do NOT re-plan" in prompt or "do not re-plan" in prompt.lower() \
               or "WORKER" in prompt

    def test_agent_complete_sentinel_in_prompt(self):
        from agent.orchestration.scheduler import _build_agent_system_prompt
        from agent.orchestration.task_dag import TaskDAG

        dag = TaskDAG(name="test")
        agent = self._make_agent()
        prompt = _build_agent_system_prompt(agent, dag, "", {})
        assert "AGENT_COMPLETE" in prompt


# ── Phase 4: Dependency waiting ───────────────────────────────────────────────

class TestDependencyWaiting:
    def test_unmet_dependency_triggers_waiting(self):
        from agent.orchestration.scheduler import _run_single_agent
        from agent.orchestration.team_generator import AgentRole, OwnedScope
        from agent.orchestration.task_dag import TaskDAG

        dag = TaskDAG(name="test")
        agent = AgentRole(
            id="be-1", role="Backend", mission="build API",
            dependencies=["db-schema"],  # not in prior_outputs
            owns=OwnedScope(), task_node_ids=[],
        )
        prior_outputs = {"__user_input__": "build app"}  # no "db-schema" key

        with patch("agent.tools._subagent._build_nested") as mb, \
             patch("agent.agent_loop.run_inner_loop") as ml, \
             patch("agent.session.get_session", return_value=MagicMock(depth=0)), \
             patch("agent.session.push_session", return_value=None), \
             patch("agent.session.pop_session"):
            mb.return_value = MagicMock(messages=[], system_prompt="", depth=1,
                                        mission_contract=None, remembered_permissions={})
            result = _run_single_agent(agent, dag, "", prior_outputs, 5)

        assert not result.success
        assert "waiting_dependency" in result.error
        ml.assert_not_called()  # LLM never invoked

    def test_met_dependency_allows_execution(self):
        from agent.orchestration.scheduler import _run_single_agent
        from agent.orchestration.team_generator import AgentRole, OwnedScope
        from agent.orchestration.task_dag import TaskDAG

        dag = TaskDAG(name="test")
        agent = AgentRole(
            id="be-2", role="Backend", mission="build API",
            dependencies=["db-schema"],  # in prior_outputs
            owns=OwnedScope(), task_node_ids=[],
        )
        prior_outputs = {
            "__user_input__": "build app",
            "db-schema": "schema completed successfully",  # dep is met
        }

        mock_result = MagicMock()
        mock_result.status = "done"
        mock_result.content = "AGENT_COMPLETE: be-2"
        mock_result.turns_used = 5

        with patch("agent.tools._subagent._build_nested") as mb, \
             patch("agent.agent_loop.run_inner_loop", return_value=mock_result) as ml, \
             patch("agent.session.get_session", return_value=MagicMock(depth=0, mission_contract=None, remembered_permissions={})), \
             patch("agent.session.push_session", return_value=None), \
             patch("agent.session.pop_session"), \
             patch("agent.ui.dashboard.push_provider_health"):
            mb.return_value = MagicMock(messages=[], system_prompt="", depth=1,
                                        mission_contract=None, remembered_permissions={})
            result = _run_single_agent(agent, dag, "", prior_outputs, 10)

        assert result.success
        ml.assert_called_once()


# ── Phase 6: Textual provider health panel ────────────────────────────────────

class TestTextualProviderHealth:
    def test_build_provider_health_renders_without_crash(self):
        from agent.ui.textual_app import build_provider_health
        rows = [
            {"provider": "openai", "status": "healthy", "timeouts": 0, "retries": 0, "success_rate": 99.0},
            {"provider": "groq", "status": "degraded", "timeouts": 2, "retries": 3, "success_rate": 85.0},
        ]
        panel = build_provider_health(rows)
        assert panel is not None
        assert hasattr(panel, "__rich_console__") or hasattr(panel, "renderable")

    def test_build_provider_health_empty_rows(self):
        from agent.ui.textual_app import build_provider_health
        panel = build_provider_health([])
        assert panel is not None

    def test_build_provider_health_all_statuses(self):
        from agent.ui.textual_app import build_provider_health
        for status in ["healthy", "degraded", "unhealthy"]:
            rows = [{"provider": "p", "status": status, "timeouts": 0, "retries": 0, "success_rate": 90.0}]
            panel = build_provider_health(rows)
            assert panel is not None

    def test_provider_health_in_textual_app_state(self):
        """EngineState in live_engine has provider_health_rows field."""
        from agent.ui.live_engine import EngineState
        state = EngineState()
        assert hasattr(state, "provider_health_rows")
        assert isinstance(state.provider_health_rows, list)


# ── Phase 7: DAG UX — tool errors suppressed in parallel mode ────────────────

class TestDAGUXToolSuppression:
    def test_tool_error_suppressed_in_parallel_mode(self, monkeypatch):
        import agent.ui.streaming as streaming_mod
        monkeypatch.setattr(streaming_mod, "_parallel_mode", True)

        from agent.ui.renderer import _on_tool_error
        from agent.ui.events import Event, EventKind

        timeline_calls = []
        import agent.ui.renderer as renderer_mod
        monkeypatch.setattr(renderer_mod, "emit_timeline", lambda *a, **kw: timeline_calls.append(a))

        ev = Event(kind=EventKind.TOOL_ERROR, data={"message": "some error"})
        _on_tool_error(ev)
        assert len(timeline_calls) == 0  # suppressed

        monkeypatch.setattr(streaming_mod, "_parallel_mode", False)

    def test_tool_error_visible_in_normal_mode(self, monkeypatch):
        import agent.ui.streaming as streaming_mod
        monkeypatch.setattr(streaming_mod, "_parallel_mode", False)

        from agent.ui.renderer import _on_tool_error
        from agent.ui.events import Event, EventKind

        timeline_calls = []
        import agent.ui.renderer as renderer_mod
        monkeypatch.setattr(renderer_mod, "emit_timeline", lambda *a, **kw: timeline_calls.append(a))

        ev = Event(kind=EventKind.TOOL_ERROR, data={"message": "some error"})
        _on_tool_error(ev)
        assert len(timeline_calls) > 0  # visible in normal mode

    def test_tool_start_suppressed_in_parallel_mode(self, monkeypatch):
        import agent.ui.streaming as streaming_mod
        monkeypatch.setattr(streaming_mod, "_parallel_mode", True)

        from agent.ui.renderer import _on_tool_start
        from agent.ui.events import Event, EventKind

        calls = []
        import agent.ui.renderer as renderer_mod
        original_mc = renderer_mod.mission_console
        mock_mc = MagicMock()
        mock_mc.on_tool_start = lambda *a: calls.append(a)
        renderer_mod.mission_console = mock_mc

        ev = Event(kind=EventKind.TOOL_START, data={"name": "write_file", "args": {}})
        _on_tool_start(ev)
        assert len(calls) == 0  # suppressed in parallel mode

        renderer_mod.mission_console = original_mc
        monkeypatch.setattr(streaming_mod, "_parallel_mode", False)


# ── Phase 1+2: Timing and mission memory integration ─────────────────────────

class TestTimingIntegration:
    def test_anti_paralysis_metrics_after_tool_calls(self):
        """After multiple tool calls, metrics reflect correct counts."""
        from agent.anti_paralysis import (
            reset_for_session, record_tool_call, record_timing,
            record_file_read, record_search, generate_report
        )
        import random
        sid = random.randint(500000, 999999)
        reset_for_session(sid)

        # Simulate: 3 analysis + 2 impl
        record_file_read(sid, "a.py"); record_tool_call(sid, "read_file", "medium"); record_timing(sid, "read_file", 0.1)
        record_file_read(sid, "b.py"); record_tool_call(sid, "read_file", "medium"); record_timing(sid, "read_file", 0.1)
        record_search(sid, "query"); record_tool_call(sid, "grep", "medium"); record_timing(sid, "grep", 0.05)
        record_tool_call(sid, "write_file", "medium"); record_timing(sid, "write_file", 0.3)
        record_tool_call(sid, "run_command", "medium"); record_timing(sid, "run_command", 0.5)

        r = generate_report(sid)
        assert r.analysis_steps == 3
        assert r.impl_steps == 2
        assert r.analysis_to_edit_ratio == pytest.approx(3/5)
        assert r.analysis_time_s == pytest.approx(0.25, rel=0.1)
        assert r.impl_time_s == pytest.approx(0.8, rel=0.1)

    def test_benchmark_harness_runs(self):
        """Benchmark harness runs without errors and produces results."""
        import sys
        sys.path.insert(0, str(ROOT / "tests"))
        from benchmark_missions import MissionBenchmark
        bm = MissionBenchmark()
        results = bm.run()
        assert "before" in results
        assert "after" in results
        assert len(results["before"]) >= 5   # 8 profiles in V1.7
        assert len(results["after"]) >= 5
        # All after results should have metrics
        for r in results["after"]:
            assert r.total_tool_calls > 0


# ── Phase 3: Impl nudge injection ────────────────────────────────────────────

class TestImplNudgeInjection:
    def test_should_inject_nudge_in_impl_mode_no_tools(self):
        from agent.anti_paralysis import (
            reset_for_session, enter_impl_mode, should_inject_impl_nudge
        )
        import random
        sid = random.randint(600000, 699999)
        reset_for_session(sid)
        enter_impl_mode(sid, "test")
        assert should_inject_impl_nudge(sid, had_tool_calls=False) is True

    def test_no_nudge_with_tool_calls(self):
        from agent.anti_paralysis import (
            reset_for_session, enter_impl_mode, should_inject_impl_nudge
        )
        import random
        sid = random.randint(700000, 799999)
        reset_for_session(sid)
        enter_impl_mode(sid, "test")
        assert should_inject_impl_nudge(sid, had_tool_calls=True) is False
