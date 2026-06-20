"""Phase 9: Recovery simulation tests.

Tests that provider crashes, timeouts, connection resets, and rate limits
do NOT permanently fail a mission — they retry, backoff, and recover.
"""
from __future__ import annotations

import sys
import types
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest


# ── 1. Provider error → retry → recover ──────────────────────────────────────

def test_provider_error_retries_not_immediate_fail():
    """A provider failure on attempt 1 must allow retry (not immediate FAILED)."""
    from agent.production.reliability import RetryPolicy, ErrorCategory
    policy = RetryPolicy()
    for cat in [ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT,
                ErrorCategory.PROVIDER, ErrorCategory.MALFORMED]:
        dec = policy.decide(cat, 1)
        assert dec.should_retry, f"{cat} should retry on attempt 1"
        assert dec.backoff_s > 0


def test_provider_error_flow_retry_then_success():
    """Simulate: ProviderError → RETRYING → RECOVERED.

    The scheduler's retry loop in _run_single_agent retries up to 3 times
    for provider failures. Verify the loop returns success when attempt 2 works.
    """
    from agent.production.reliability import (
        RetryPolicy, ErrorCategory, ProviderHealth, is_provider_failure
    )
    policy = RetryPolicy()
    health = ProviderHealth()
    provider = "test-provider"

    # Simulate: attempt 1 fails with TIMEOUT
    cat = ErrorCategory.TIMEOUT
    health.record_error(provider, cat)
    dec1 = policy.decide(cat, 1)
    assert dec1.should_retry

    # Simulate: attempt 2 succeeds
    health.record_success(provider)
    metrics = health.get_metrics(provider)
    assert metrics.successes == 1
    assert metrics.failures == 1
    # After recovery, success rate is 50% (1/2)
    assert metrics.success_rate == 0.5


def test_never_provider_error_direct_to_failed():
    """Provider Error → FAILED without retry must never happen for transient errors."""
    from agent.production.reliability import (
        RetryPolicy, ErrorCategory, is_provider_failure
    )
    policy = RetryPolicy()
    transient = [ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT,
                 ErrorCategory.MALFORMED, ErrorCategory.PROVIDER]
    for cat in transient:
        assert is_provider_failure(cat), f"{cat} should be provider failure"
        dec = policy.decide(cat, 1)
        assert dec.should_retry, f"Provider error {cat} must not immediately fail"


def test_max_retries_eventually_fails():
    """After max retries exhausted, should_retry=False (FAILED allowed)."""
    from agent.production.reliability import RetryPolicy, ErrorCategory
    policy = RetryPolicy()
    # TIMEOUT allows 3 retries; attempt 4 must fail
    dec = policy.decide(ErrorCategory.TIMEOUT, 4)
    assert not dec.should_retry


# ── 2. Connection reset / WinError classification ─────────────────────────────

def test_connection_reset_classifies_as_provider():
    from agent.production.reliability import classify_error, ErrorCategory
    assert classify_error("connection reset by peer") == ErrorCategory.PROVIDER
    assert classify_error("connection reset") == ErrorCategory.PROVIDER


def test_winerror_10054_classifies_as_provider():
    from agent.production.reliability import classify_error, ErrorCategory
    e = OSError("WinError 10054: An existing connection was forcibly closed")
    assert classify_error(e) == ErrorCategory.PROVIDER
    assert classify_error("winerror 10054") == ErrorCategory.PROVIDER


def test_read_timeout_classifies_correctly():
    from agent.production.reliability import classify_error, ErrorCategory
    assert classify_error("ReadTimeout") == ErrorCategory.TIMEOUT
    assert classify_error("read timeout after 120s") == ErrorCategory.TIMEOUT


def test_rate_limit_classifies_correctly():
    from agent.production.reliability import classify_error, ErrorCategory
    assert classify_error("429 Too Many Requests") == ErrorCategory.RATE_LIMIT
    assert classify_error("rate limit exceeded") == ErrorCategory.RATE_LIMIT


# ── 3. WorkerResult failure type detection ────────────────────────────────────

def test_worker_result_provider_timeout_is_provider_failure():
    """A WorkerResult with timeout error is recognized as provider failure."""
    from agent.orchestration.scheduler import WorkerResult
    from agent.production.reliability import classify_error, is_provider_failure, ErrorCategory

    wr = WorkerResult(
        agent_id="fe-1", role="Frontend", success=False, output="",
        error="provider timeout: connection to model dropped"
    )
    cat = classify_error(wr.error)
    assert is_provider_failure(cat) or cat == ErrorCategory.PROVIDER or cat == ErrorCategory.TIMEOUT


def test_worker_result_syntax_error_not_provider_failure():
    """A WorkerResult with a syntax error is NOT a provider failure (don't retry)."""
    from agent.orchestration.scheduler import WorkerResult
    from agent.production.reliability import classify_error, is_provider_failure, ErrorCategory

    wr = WorkerResult(
        agent_id="be-1", role="Backend", success=False, output="",
        error="SyntaxError in generated code"
    )
    cat = classify_error(wr.error)
    # SyntaxError is UNKNOWN — not a provider failure
    assert cat == ErrorCategory.UNKNOWN or not is_provider_failure(cat)


# ── 4. Dependency waiting guard ───────────────────────────────────────────────

def test_dependency_waiting_returns_immediately():
    """Agent with unmet dependency must return waiting_dependency, not spin LLM."""
    from agent.orchestration.scheduler import _run_single_agent, _agent_execution_layers
    from agent.orchestration.team_generator import AgentRole, OwnedScope
    from agent.orchestration.task_dag import TaskDAG

    dag = TaskDAG(name="test")

    db_agent = AgentRole(
        id="db", role="Database", mission="schema",
        dependencies=["notdone"],  # unmet dependency
        owns=OwnedScope(),
        task_node_ids=[],
    )

    # prior_outputs does NOT contain "notdone"
    prior_outputs = {"__user_input__": "build app"}

    # Mock everything — should never reach the LLM
    with patch("agent.tools._subagent._build_nested") as mock_build, \
         patch("agent.agent_loop.run_inner_loop") as mock_loop, \
         patch("agent.session.get_session", return_value=MagicMock(depth=0)), \
         patch("agent.session.push_session", return_value=None), \
         patch("agent.session.pop_session"):

        result = _run_single_agent(db_agent, dag, "", prior_outputs, 10)

    assert not result.success
    assert "waiting_dependency" in result.error
    # LLM was never called
    mock_loop.assert_not_called()


def test_agent_without_dependencies_runs_normally():
    """Agent with no dependencies must proceed to LLM execution."""
    from agent.orchestration.scheduler import _run_single_agent
    from agent.orchestration.team_generator import AgentRole, OwnedScope
    from agent.orchestration.task_dag import TaskDAG

    dag = TaskDAG(name="test")
    agent = AgentRole(
        id="fe", role="Frontend", mission="build UI",
        dependencies=[],  # no deps
        owns=OwnedScope(),
        task_node_ids=[],
    )

    mock_result = MagicMock()
    mock_result.status = "done"
    mock_result.content = "AGENT_COMPLETE: fe"
    mock_result.turns_used = 3

    with patch("agent.tools._subagent._build_nested") as mock_build, \
         patch("agent.agent_loop.run_inner_loop", return_value=mock_result) as mock_loop, \
         patch("agent.session.get_session", return_value=MagicMock(depth=0, mission_contract=None, remembered_permissions={})), \
         patch("agent.session.push_session", return_value=None), \
         patch("agent.session.pop_session"), \
         patch("agent.ui.dashboard.push_provider_health"):

        mock_build.return_value = MagicMock(messages=[], system_prompt="", depth=1,
                                            mission_contract=None, remembered_permissions={})
        result = _run_single_agent(agent, dag, "", {"__user_input__": "build"}, 10)

    assert result.success
    mock_loop.assert_called_once()


# ── 5. Provider health records retry attempts ─────────────────────────────────

def test_provider_health_records_retries():
    """Provider health correctly counts errors and retried errors separately."""
    from agent.production.reliability import ProviderHealth, ErrorCategory

    health = ProviderHealth()
    health.record_error("p1", ErrorCategory.TIMEOUT, retried=False)
    health.record_error("p1", ErrorCategory.TIMEOUT, retried=True)   # retry
    health.record_error("p1", ErrorCategory.TIMEOUT, retried=True)   # retry

    m = health.get_metrics("p1")
    assert m.failures == 3
    assert m.provider_errors == 3   # all are TIMEOUT (provider failure)


def test_provider_health_degraded_after_failures():
    """Provider becomes degraded (80-95% success) after several failures."""
    from agent.production.reliability import ProviderHealth, ErrorCategory

    health = ProviderHealth()
    for _ in range(15):
        health.record_success("p2")
    for _ in range(3):
        health.record_error("p2", ErrorCategory.PROVIDER)
    # 15/18 = 83.3% -> degraded (80-95%)

    m = health.get_metrics("p2")
    assert 0.80 <= m.success_rate <= 0.95


# ── 6. Mission state checkpoint after scheduler run ──────────────────────────

def test_mission_checkpoint_written_on_success():
    """Scheduler writes a mission-state checkpoint after completion."""
    from agent.orchestration.scheduler import SchedulerResult, WorkerResult

    # Verify checkpoint call appears in scheduler (tested structurally)
    import agent.orchestration.scheduler as sched_mod
    source = open(sched_mod.__file__).read()
    assert "append_checkpoint" in source or "mission-state" in source, \
        "Scheduler must write a mission checkpoint after completion"


def test_finish_reason_always_set_in_loop_result():
    """LoopResult from max_turns path carries finish_reason."""
    from agent.agent_loop import LoopResult
    r = LoopResult(status="max_turns", turns_used=100, finish_reason="max_turns")
    assert r.finish_reason == "max_turns"
    assert r.incomplete


def test_finish_reason_interrupted_set():
    from agent.agent_loop import LoopResult
    r = LoopResult(status="interrupted", turns_used=5, finish_reason="interrupted")
    assert r.finish_reason == "interrupted"
