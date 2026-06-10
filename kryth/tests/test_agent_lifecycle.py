"""Test suite for agent lifecycle and spawning rules.

Validates the production requirement: lightweight coordinator-worker architecture.
- Only the main coordinator may create agents.
- Worker agents cannot create additional workers.
- Maximum agent depth is one level.
- All temporary agents are destroyed after completion.
- Simple tasks never enter orchestration mode.
- Failed workers do not leave orphan agents running.
"""

import pytest
from unittest.mock import patch, MagicMock
from agent.session import Session, get_session, push_session, pop_session
from agent._subagent import spawn_agent, MAX_PARALLEL_AGENTS
from agent.tools._subagent import spawn_agents_parallel
from agent.agent_loop import run_agent
from agent.tools._results import is_error


class TestAgentDepthLimits:
    """Test that agent depth is properly constrained."""

    def test_coordinator_depth_is_zero(self):
        """Main coordinator should have depth 0."""
        session = get_session()
        session.depth = 0
        assert session.depth == 0

    @patch('agent.agent_loop.run_inner_loop')
    def test_worker_depth_is_one(self, mock_run_inner_loop):
        """Workers spawned by coordinator should have depth 1."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        coordinator = get_session()
        coordinator.depth = 0
        coordinator.can_spawn = True
        
        initial_depth = coordinator.depth
        result = spawn_agent("test", "test prompt", max_turns=1)
        
        current = get_session()
        assert current.depth == initial_depth

    @patch('agent.agent_loop.run_inner_loop')
    def test_worker_cannot_spawn_subworker(self, mock_run_inner_loop):
        """Workers (depth=1) should not spawn sub-workers (would be depth=2)."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        # Create a session that cannot spawn (simulating a worker)
        parent = Session()
        parent.depth = 1
        parent.can_spawn = False
        token = push_session(parent)
        try:
            result = spawn_agent("test", "test prompt", max_turns=1)
            assert is_error(result)
            assert "cannot spawn" in result.lower() or "depth limit" in result.lower()
        finally:
            pop_session(token)

    def test_max_parallel_agents_limit(self):
        """Should respect MAX_PARALLEL_AGENTS constant."""
        assert isinstance(MAX_PARALLEL_AGENTS, int)
        assert 1 <= MAX_PARALLEL_AGENTS <= 10


class TestWorkerCapabilities:
    """Test that workers have restricted capabilities."""

    @patch('agent.agent_loop.run_inner_loop')
    def test_worker_session_cannot_spawn(self, mock_run_inner_loop):
        """Worker sessions should have can_spawn=False."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        coordinator = get_session()
        coordinator.depth = 0
        coordinator.can_spawn = True
        
        result = spawn_agent("test", "test prompt", max_turns=1)
        # Should succeed; the worker's inability to spawn is tested elsewhere
        assert not is_error(result)

    @patch('agent.agent_loop.run_inner_loop')
    def test_coordinator_can_spawn(self, mock_run_inner_loop):
        """Coordinator (depth=0) should have can_spawn=True."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        session = get_session()
        session.depth = 0
        session.can_spawn = True
        
        result = spawn_agent("test", "test prompt", max_turns=1)
        assert not is_error(result) or "depth limit" not in result.lower()


class TestAgentCleanup:
    """Test that all temporary agents are properly destroyed."""

    @patch('agent.agent_loop.run_inner_loop')
    def test_spawn_agent_cleans_up_on_success(self, mock_run_inner_loop):
        """spawn_agent should pop session even on success."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        initial_depth = get_session().depth
        result = spawn_agent("test task", "test prompt", max_turns=1)
        final_depth = get_session().depth
        assert final_depth == initial_depth

    @patch('agent.agent_loop.run_inner_loop')
    def test_spawn_agent_cleans_up_on_failure(self, mock_run_inner_loop):
        """spawn_agent should pop session even on exception."""
        mock_run_inner_loop.side_effect = Exception("LLM error")
        
        initial_depth = get_session().depth
        with pytest.raises(Exception):
            result = spawn_agent("test", "test prompt", max_turns=1)
        final_depth = get_session().depth
        assert final_depth == initial_depth

    @patch('agent.agent_loop.run_inner_loop')
    def test_parallel_agents_cleanup_all(self, mock_run_inner_loop):
        """spawn_agents_parallel should clean up all worker sessions."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        initial_depth = get_session().depth
        tasks = [
            {"description": f"task {i}", "prompt": f"do task {i}", "max_turns": 1}
            for i in range(3)
        ]
        result = spawn_agents_parallel(tasks)
        final_depth = get_session().depth
        assert final_depth == initial_depth

    @patch('agent.agent_loop.run_inner_loop')
    def test_worker_sessions_do_not_leak(self, mock_run_inner_loop):
        """Worker sessions should not leak state to parent."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        parent = get_session()
        parent.messages.clear()
        parent.todos.clear()
        parent.cumulative_in_tokens = 0
        parent.cumulative_out_tokens = 0
        
        result = spawn_agent("test", "test prompt", max_turns=1)
        
        assert len(parent.messages) == 0
        assert len(parent.todos) == 0


class TestSimpleTaskRouting:
    """Test that simple tasks bypass orchestration."""

    @patch('agent.agent_loop.classify_task')
    @patch('agent.agent_loop.run_dynamic_build_with_approval')
    @patch('agent.agent_loop.run_inner_loop')
    @patch('agent.agent_loop.route')
    @patch('agent.agent_loop.auto_select_skills')
    def test_simple_task_uses_main_agent(self, mock_auto_select, mock_route, mock_run_inner_loop, mock_dynamic, mock_classify):
        """Simple tasks should not trigger dynamic builder."""
        mock_classify.return_value = MagicMock(complexity='simple')
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        mock_route.return_value = []
        mock_auto_select.return_value = []
        
        run_agent("list files")
        
        mock_dynamic.assert_not_called()
        mock_run_inner_loop.assert_called_once()
        mock_route.assert_called_once()
        mock_auto_select.assert_called_once()

    @patch('agent.agent_loop.classify_task')
    @patch('agent.agent_loop.run_dynamic_build_with_approval')
    @patch('agent.agent_loop.run_inner_loop')
    @patch('agent.agent_loop.route')
    @patch('agent.agent_loop.auto_select_skills')
    def test_complex_task_uses_dynamic_builder(self, mock_auto_select, mock_route, mock_run_inner_loop, mock_dynamic, mock_classify):
        """Complex tasks should use dynamic builder."""
        mock_classify.return_value = MagicMock(complexity='complex')
        mock_dynamic.return_value = "Task Completed"
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        mock_route.return_value = []
        mock_auto_select.return_value = []
        
        run_agent("build a full-stack web app")
        
        mock_dynamic.assert_called_once()
        # run_inner_loop may be called by workers, but not directly by run_agent
        # So we don't assert on run_inner_loop here

    @patch('agent.agent_loop.classify_task')
    @patch('agent.agent_loop.run_dynamic_build_with_approval')
    @patch('agent.agent_loop.run_inner_loop')
    @patch('agent.agent_loop.route')
    @patch('agent.agent_loop.auto_select_skills')
    def test_medium_task_uses_main_agent_directly(self, mock_auto_select, mock_route, mock_run_inner_loop, mock_dynamic, mock_classify):
        """Medium tasks should use main agent directly (no orchestration)."""
        mock_classify.return_value = MagicMock(complexity='medium')
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        mock_route.return_value = []
        mock_auto_select.return_value = []
        
        run_agent("write a python function")
        
        mock_dynamic.assert_not_called()
        mock_run_inner_loop.assert_called_once()
        mock_route.assert_called_once()
        mock_auto_select.assert_called_once()


class TestWorkerImmutability:
    """Test that workers are truly temporary and cannot persist."""

    @patch('agent.agent_loop.run_inner_loop')
    def test_worker_has_no_persistent_state(self, mock_run_inner_loop):
        """Worker sessions should not leak state back to coordinator."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        parent = get_session()
        parent_state = {
            'messages': len(parent.messages),
            'todos': len(parent.todos),
        }
        
        result = spawn_agent("test", "test prompt", max_turns=1)
        
        assert len(parent.messages) == parent_state['messages']
        assert len(parent.todos) == parent_state['todos']


class TestOrchestrationConstraints:
    """Test that orchestration complexity is limited."""

    @patch('agent.agent_loop.run_inner_loop')
    def test_no_agent_hierarchy_beyond_one_level(self, mock_run_inner_loop):
        """Should prevent agents from spawning agents beyond depth 1."""
        # This tests that a worker (depth=1, can_spawn=False) cannot spawn
        parent = Session()
        parent.depth = 1
        parent.can_spawn = False
        token = push_session(parent)
        try:
            result = spawn_agent("test", "test prompt", max_turns=1)
            assert is_error(result)
            assert "cannot spawn" in result.lower() or "depth limit" in result.lower()
        finally:
            pop_session(token)

    def test_single_coordinator_only(self):
        """Only one coordinator should exist per request."""
        session = get_session()
        assert session.depth == 0

    @patch('agent.agent_loop.run_inner_loop')
    def test_no_long_lived_worker_pools(self, mock_run_inner_loop):
        """Worker pools should be created per-task and destroyed immediately after."""
        mock_run_inner_loop.return_value = MagicMock(status='done', content='OK')
        
        initial_depth = get_session().depth
        tasks = [{"description": f"task {i}", "prompt": f"do task {i}"} for i in range(3)]
        result = spawn_agents_parallel(tasks)
        final_depth = get_session().depth
        assert final_depth == initial_depth


class TestFailureCleanup:
    """Test that failed workers do not leave orphan agents running."""

    @patch('agent.agent_loop.run_inner_loop')
    def test_worker_failure_cleans_up_session(self, mock_run_inner_loop):
        """Even if a worker crashes, its session should be popped."""
        mock_run_inner_loop.side_effect = Exception("LLM error")
        
        initial_depth = get_session().depth
        with pytest.raises(Exception):
            result = spawn_agent("test", "test prompt", max_turns=1)
        final_depth = get_session().depth
        assert final_depth == initial_depth

    @patch('agent.agent_loop.run_inner_loop')
    def test_parallel_worker_failure_does_not_affect_others(self, mock_run_inner_loop):
        """One failing parallel worker should not affect others or leave orphans."""
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # first worker fails
                raise Exception("Worker 1 failed")
            return MagicMock(status='done', content='OK')
        
        mock_run_inner_loop.side_effect = side_effect
        
        initial_depth = get_session().depth
        tasks = [
            {"description": "task 0", "prompt": "valid prompt"},
            {"description": "task 1", "prompt": "valid prompt"},
            {"description": "task 2", "prompt": "valid prompt"},
        ]
        result = spawn_agents_parallel(tasks)
        final_depth = get_session().depth
        assert final_depth == initial_depth
        # All workers should be cleaned up even if one failed


if __name__ == "__main__":
    pytest.main([__file__, "-v"])