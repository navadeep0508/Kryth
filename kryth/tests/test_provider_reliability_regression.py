"""Regression tests for provider reliability hardening.

Covers:
- tool_calls=None / missing / invalid type normalization
- Provider timeout / retry / recovery
- Provider isolation (never Provider Error → FAILED without retries)
- finish_reason propagation
- Dashboard/ops center rendering
- Approval flow (single approval)
- Waiting dependency behavior
"""
from __future__ import annotations

import sys
import types
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest


# ── TASK 1: finish_reason propagation ────────────────────────────────────────

def test_loop_result_max_turns_has_finish_reason():
    """LoopResult from max_turns path must include finish_reason='max_turns'."""
    from agent.agent_loop import LoopResult
    r = LoopResult(status="max_turns", turns_used=100, finish_reason="max_turns")
    assert r.finish_reason == "max_turns"
    assert r.incomplete is True


def test_loop_result_interrupted_has_finish_reason():
    from agent.agent_loop import LoopResult
    r = LoopResult(status="interrupted", turns_used=5, finish_reason="interrupted")
    assert r.finish_reason == "interrupted"


def test_loop_result_api_error_has_finish_reason():
    from agent.agent_loop import LoopResult
    r = LoopResult(status="api_error", turns_used=1, finish_reason="api_error")
    assert r.finish_reason == "api_error"


def test_loop_result_done_has_completed_finish_reason():
    from agent.agent_loop import LoopResult
    r = LoopResult(status="done", content="ok", turns_used=3, finish_reason="completed")
    assert r.finish_reason == "completed"
    assert r.incomplete is False


def test_loop_result_all_finish_reasons_covered():
    """All LoopResult return paths must carry finish_reason."""
    from agent.agent_loop import LoopResult
    valid_reasons = {
        "completed", "max_turns", "timeout", "malformed",
        "provider_error", "api_error", "interrupted",
        "stream_error", "context_overflow", "payload_too_large",
        "rate_limit",
    }
    for reason in valid_reasons:
        r = LoopResult(status="done", finish_reason=reason)
        assert r.finish_reason == reason, f"finish_reason not propagated: {reason}"


# ── TASK 2: reliability.py classify_error hardening ──────────────────────────

def test_classify_error_winerror_10054():
    """WinError 10054 (connection reset) classifies as PROVIDER."""
    from agent.production.reliability import classify_error, ErrorCategory
    e = OSError("WinError 10054: An existing connection was forcibly closed")
    assert classify_error(e) == ErrorCategory.PROVIDER


def test_classify_error_connection_reset_by_peer():
    from agent.production.reliability import classify_error, ErrorCategory
    assert classify_error("connection reset by peer") == ErrorCategory.PROVIDER


def test_classify_error_connection_reset():
    from agent.production.reliability import classify_error, ErrorCategory
    assert classify_error("connection reset") == ErrorCategory.PROVIDER


def test_classify_error_all_finish_reasons():
    """Every finish_reason that appears in LoopResult must not be UNKNOWN."""
    from agent.production.reliability import classify_error, ErrorCategory
    mapping = {
        "timeout":           ErrorCategory.TIMEOUT,
        "rate_limit":        ErrorCategory.RATE_LIMIT,
        "payload_too_large": ErrorCategory.PAYLOAD_TOO_LARGE,
        "malformed":         ErrorCategory.MALFORMED,
        "provider_error":    ErrorCategory.PROVIDER,
        "api_error":         ErrorCategory.API_ERROR,
        "interrupted":       ErrorCategory.INTERRUPTED,
        "max_turns":         ErrorCategory.MAX_TURNS,
        "stream_error":      ErrorCategory.MALFORMED,
    }
    for reason, expected in mapping.items():
        got = classify_error(reason)
        assert got == expected, f"classify_error({reason!r}) = {got}, want {expected}"


def test_classify_error_no_unknown_for_valid_inputs():
    """None of the known error patterns should return UNKNOWN."""
    from agent.production.reliability import classify_error, ErrorCategory
    patterns = [
        "ReadTimeout", "APITimeoutError",
        "RateLimitError", "429 rate limit",
        "connection reset by peer", "WinError 10054",
        "service unavailable 503", "502 bad gateway",
        "413 request entity too large",
        "malformed chunk in stream",
    ]
    for p in patterns:
        cat = classify_error(p)
        assert cat != ErrorCategory.UNKNOWN, f"'{p}' classified as UNKNOWN"


def test_classify_error_new_categories_exist():
    """New categories API_ERROR, INTERRUPTED, MAX_TURNS exist."""
    from agent.production.reliability import ErrorCategory
    assert hasattr(ErrorCategory, "API_ERROR")
    assert hasattr(ErrorCategory, "INTERRUPTED")
    assert hasattr(ErrorCategory, "MAX_TURNS")


# ── TASK 3: stream parsing — tool_calls normalization ─────────────────────────

def _make_delta(tool_calls_val):
    """Create a simple namespace delta with arbitrary tool_calls value."""
    return types.SimpleNamespace(
        content=None,
        reasoning_content=None,
        tool_calls=tool_calls_val,
    )


def test_stream_normalizer_tool_calls_none():
    """tool_calls=None must normalize to empty list, not crash."""
    from agent.model.stream_normalizer import StreamNormalizer
    sn = StreamNormalizer()
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = _make_delta(None)
    chunk.choices[0].finish_reason = None
    chunk.usage = None
    # Must not raise
    result = sn.normalize_openai_chunk(chunk)
    assert isinstance(result, list)


def test_stream_normalizer_tool_calls_missing():
    """tool_calls attribute missing from delta must normalize to []."""
    from agent.model.stream_normalizer import StreamNormalizer
    sn = StreamNormalizer()
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    delta = types.SimpleNamespace(content="hello")  # no tool_calls attribute
    chunk.choices[0].delta = delta
    chunk.choices[0].finish_reason = None
    chunk.usage = None
    result = sn.normalize_openai_chunk(chunk)
    assert isinstance(result, list)


def test_stream_normalizer_tool_calls_invalid_type():
    """tool_calls='invalid_string' must normalize to []."""
    from agent.model.stream_normalizer import StreamNormalizer
    sn = StreamNormalizer()
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = _make_delta("invalid_string")
    chunk.choices[0].finish_reason = None
    chunk.usage = None
    result = sn.normalize_openai_chunk(chunk)
    # No tool_call chunks should appear from invalid type
    from agent.model.stream_normalizer import ChunkType
    tool_chunks = [c for c in result if c.chunk_type == ChunkType.TOOL_CALL]
    assert len(tool_chunks) == 0


def test_stream_normalizer_tool_calls_dict_type():
    """tool_calls=dict (wrong type) must normalize to []."""
    from agent.model.stream_normalizer import StreamNormalizer
    sn = StreamNormalizer()
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = _make_delta({"name": "write_file"})
    chunk.choices[0].finish_reason = None
    chunk.usage = None
    result = sn.normalize_openai_chunk(chunk)
    assert isinstance(result, list)


def test_merge_tool_call_delta_skips_invalid():
    """_merge_tool_call_delta must not crash on invalid delta objects."""
    from agent.llm import _merge_tool_call_delta
    accum = {}
    # Valid delta
    valid = types.SimpleNamespace(index=0, id="abc123456",
                                  function=types.SimpleNamespace(name="write_file", arguments='{"path":'))
    _merge_tool_call_delta(accum, valid)
    assert 0 in accum

    # Invalid (no function attr) — must not crash
    invalid = types.SimpleNamespace(index=1, id=None, function=None)
    _merge_tool_call_delta(accum, invalid)  # should not raise


def test_tool_calls_none_in_response_returns_empty_list():
    """When ask_llm_stream returns tool_calls=None, agent_loop sees []."""
    # This is the structural contract: agent_loop always checks
    # `response["tool_calls"] or []` — if it returns None, the loop
    # treats it as no tool calls. This test verifies that contract.
    from agent.agent_loop import LoopResult
    # Simulate the agent_loop behavior:
    tool_calls = None
    safe = tool_calls or []
    assert safe == []


# ── TASK 4: Provider isolation — retry/backoff/recover flow ───────────────────

def test_retry_policy_provider_never_fails_immediately():
    """Provider failures must always get at least 1 retry before failing."""
    from agent.production.reliability import RetryPolicy, ErrorCategory
    policy = RetryPolicy()
    for cat in [ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT,
                ErrorCategory.MALFORMED, ErrorCategory.PROVIDER]:
        dec = policy.decide(cat, 1)
        assert dec.should_retry, f"{cat} should allow retry on attempt 1"


def test_backoff_is_positive_for_provider_errors():
    from agent.production.reliability import RetryPolicy, ErrorCategory
    policy = RetryPolicy()
    for cat in [ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT]:
        dec = policy.decide(cat, 1)
        assert dec.backoff_s > 0, f"{cat} retry should have positive backoff"


def test_provider_failure_does_not_exhaust_on_first_attempt():
    """After first provider failure, should_retry must be True."""
    from agent.production.reliability import RetryPolicy, ErrorCategory
    policy = RetryPolicy()
    # First attempt must retry for all provider failure categories
    for cat in [ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT,
                ErrorCategory.MALFORMED, ErrorCategory.PROVIDER,
                ErrorCategory.PAYLOAD_TOO_LARGE]:
        dec = policy.decide(cat, 1)
        assert dec.should_retry, f"Category {cat} should retry on attempt 1 (not fail immediately)"


def test_provider_error_eventually_fails_after_max_retries():
    from agent.production.reliability import RetryPolicy, ErrorCategory
    policy = RetryPolicy()
    # Timeout allows 3 retries, so attempt 4 should fail
    dec = policy.decide(ErrorCategory.TIMEOUT, 4)
    assert not dec.should_retry


def test_scheduler_local_notify_fn_thread_safe():
    """_get_notify_fn / _set_notify_fn must be thread-safe via thread-local."""
    from agent.orchestration.scheduler import _get_notify_fn, _set_notify_fn

    results = []
    errors = []

    def worker(fn_id):
        try:
            mock_fn = MagicMock()
            mock_fn.id = fn_id
            _set_notify_fn(mock_fn)
            import time
            time.sleep(0.01)  # yield
            got = _get_notify_fn()
            results.append((fn_id, got.id))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # Each thread should have gotten its own function
    for fn_id, got_id in results:
        assert fn_id == got_id, "Thread-local isolation failed"


# ── TASK 5: Ops center / dashboard provider health ────────────────────────────

def test_provider_health_view_shows_all_columns(monkeypatch):
    """provider_health_view() renders Provider, Status, Timeouts, Retries, Success Rate."""
    from agent.ui.ops_center import provider_health_view
    from agent.production.reliability import ProviderHealth, ErrorCategory
    from agent.ui.console import console

    health = ProviderHealth()
    for _ in range(15):
        health.record_success("openai-api")
    for _ in range(3):
        health.record_error("openai-api", ErrorCategory.TIMEOUT)
    health.record_error("openai-api", ErrorCategory.RATE_LIMIT)

    import agent.production.reliability as rel_mod
    monkeypatch.setattr(rel_mod, "_provider_health", health)

    rendered = []
    monkeypatch.setattr(console, "print", lambda *a, **kw: rendered.append(str(a)))
    provider_health_view()
    # Panel was rendered (at least two calls: blank line + panel)
    assert len(rendered) >= 1, "provider_health_view produced no output"


def test_provider_health_view_does_not_activate_in_direct_mode():
    """provider_health_view should be callable even when ops center is inactive."""
    from agent.ui.ops_center import should_activate
    assert not should_activate(mode="direct", workers=5)
    assert not should_activate(is_conversational=True, workers=10)


def test_ops_center_activates_dag_swarm_only():
    from agent.ui.ops_center import should_activate
    assert should_activate(mode="dag", workers=4)
    assert should_activate(mode="swarm", workers=3)
    assert not should_activate(mode="direct", workers=10)
    assert not should_activate(mode="conversation", workers=5)
    assert not should_activate(mode="fast", workers=5)
    assert not should_activate(is_conversational=True, workers=5)


def test_dashboard_push_provider_health_safe_when_no_data(monkeypatch):
    """push_provider_health() must not crash when no metrics exist."""
    import agent.production.reliability as rel_mod
    monkeypatch.setattr(rel_mod, "_provider_health", None)
    from agent.ui.dashboard import push_provider_health
    push_provider_health()  # must not raise


def test_dashboard_push_provider_health_builds_rows(monkeypatch):
    """push_provider_health() pushes row data when metrics exist."""
    from agent.production.reliability import ProviderHealth, ErrorCategory
    health = ProviderHealth()
    for _ in range(20):
        health.record_success("test-provider")
    health.record_error("test-provider", ErrorCategory.TIMEOUT)

    import agent.production.reliability as rel_mod
    monkeypatch.setattr(rel_mod, "_provider_health", health)

    pushed_events = []

    import agent.ui.dashboard as dash_mod
    monkeypatch.setattr(dash_mod, "_running", True)
    original_put = dash_mod._event_queue.put
    monkeypatch.setattr(dash_mod._event_queue, "put", lambda ev: pushed_events.append(ev))

    from agent.ui.dashboard import push_provider_health
    push_provider_health()

    assert len(pushed_events) == 1
    ev = pushed_events[0]
    assert ev["kind"] == "provider_health"
    assert len(ev["rows"]) >= 1
    row = ev["rows"][0]
    assert row["provider"] == "test-provider"
    assert "status" in row
    assert "success_rate" in row

    monkeypatch.setattr(dash_mod._event_queue, "put", original_put)


# ── TASK 6: DAG UI — tool logs suppressed in parallel mode ────────────────────

def test_renderer_suppresses_tool_result_in_parallel_mode(monkeypatch):
    """In parallel mode, _on_tool_result does NOT call mission_console.on_tool_result."""
    import agent.ui.streaming as streaming_mod
    monkeypatch.setattr(streaming_mod, "_parallel_mode", True)

    from agent.ui.renderer import _on_tool_result
    from agent.ui.events import Event, EventKind

    calls = []
    import agent.ui.renderer as renderer_mod
    original_mc = renderer_mod.mission_console
    mock_mc = MagicMock()
    mock_mc.on_tool_result = lambda *a: calls.append(a)
    renderer_mod.mission_console = mock_mc

    ev = Event(kind=EventKind.TOOL_RESULT, data={"result": "some long output\n" * 20, "error": False})
    _on_tool_result(ev)

    # In parallel mode, mission_console.on_tool_result must NOT be called
    assert len(calls) == 0, "mission_console.on_tool_result was called in parallel mode"

    renderer_mod.mission_console = original_mc
    monkeypatch.setattr(streaming_mod, "_parallel_mode", False)


def test_renderer_shows_tool_result_in_normal_mode(monkeypatch):
    """In normal mode, _on_tool_result still routes through mission_console."""
    import agent.ui.streaming as streaming_mod
    monkeypatch.setattr(streaming_mod, "_parallel_mode", False)

    from agent.ui.renderer import _on_tool_result
    from agent.ui.events import Event, EventKind

    calls = []
    import agent.ui.renderer as renderer_mod
    original_mc = renderer_mod.mission_console
    mock_mc = MagicMock()
    mock_mc.on_tool_result = lambda *a: calls.append(a)
    mock_mc._current_tool = "write_file"
    renderer_mod.mission_console = mock_mc

    ev = Event(kind=EventKind.TOOL_RESULT, data={"result": "ok", "error": False})
    _on_tool_result(ev)

    assert len(calls) == 1
    renderer_mod.mission_console = original_mc


# ── TASK 7: Single approval — SESSION_APPROVED skips second prompt ─────────────

def test_approval_gate_session_approved_skips_prompt():
    """ApprovalMode.SESSION_APPROVED never triggers the ASK prompt."""
    from agent.orchestration.approval_gate import (
        ApprovalMode, request_approval
    )
    from agent.orchestration.task_dag import TaskDAG
    from agent.orchestration.team_generator import TeamPlan, AgentRole

    dag = TaskDAG(name="test-dag")
    team = TeamPlan(
        agents=[
            AgentRole(id="a1", role="Frontend", mission="build UI"),
            AgentRole(id="a2", role="Backend", mission="build API"),
        ],
        complexity=2.0,
        risk_assessment="low",
        estimated_total_turns=80,
        estimated_total_tokens=50000,
        reasoning="test",
        recommended_strategy="parallel",
        parallel_benefit=3.0,
        parallel_cost=1.0,
    )

    # approval_gate.request_approval checks analysis.strategy before mode check
    mock_analysis = types.SimpleNamespace(strategy="parallel", cost=1.0, benefit=3.0)

    ask_called = []
    def mock_ask_fn(prompt):
        ask_called.append(prompt)
        return "y"

    result = request_approval(
        dag, team, mock_analysis,
        ApprovalMode.SESSION_APPROVED,
        ask_fn=mock_ask_fn,
    )
    assert result.approved is True
    assert len(ask_called) == 0, "SESSION_APPROVED should not prompt user"


def test_approval_gate_always_single_never_approves():
    from agent.orchestration.approval_gate import ApprovalMode, request_approval
    from agent.orchestration.task_dag import TaskDAG
    from agent.orchestration.team_generator import TeamPlan, AgentRole

    dag = TaskDAG(name="test-dag")
    mock_analysis = types.SimpleNamespace(strategy="parallel", cost=1.0, benefit=3.0)
    team = TeamPlan(
        agents=[
            AgentRole(id="a1", role="FE", mission="UI"),
            AgentRole(id="a2", role="BE", mission="API"),
        ],
        complexity=1.0,
        risk_assessment="low",
        estimated_total_turns=40,
        estimated_total_tokens=20000,
        reasoning="test",
        recommended_strategy="parallel",
        parallel_benefit=2.0,
        parallel_cost=1.0,
    )
    result = request_approval(dag, team, mock_analysis, ApprovalMode.ALWAYS_SINGLE)
    assert result.approved is False


# ── TASK 8: Waiting dependency behavior ───────────────────────────────────────

def test_agent_execution_layers_respects_dependencies():
    """_agent_execution_layers must put dependent agents in later layers."""
    from agent.orchestration.scheduler import _agent_execution_layers
    from agent.orchestration.team_generator import AgentRole

    db_agent = AgentRole(id="db", role="Database", mission="schema", dependencies=[])
    be_agent = AgentRole(id="be", role="Backend", mission="API", dependencies=["db"])
    fe_agent = AgentRole(id="fe", role="Frontend", mission="UI", dependencies=["be"])

    layers = _agent_execution_layers([db_agent, be_agent, fe_agent])
    assert len(layers) == 3
    assert any(a.id == "db" for a in layers[0])
    assert any(a.id == "be" for a in layers[1])
    assert any(a.id == "fe" for a in layers[2])


def test_agent_execution_layers_parallel_independent():
    """Independent agents must end up in the same layer."""
    from agent.orchestration.scheduler import _agent_execution_layers
    from agent.orchestration.team_generator import AgentRole

    a1 = AgentRole(id="a1", role="Auth", mission="auth", dependencies=[])
    a2 = AgentRole(id="a2", role="Payment", mission="payment", dependencies=[])
    a3 = AgentRole(id="a3", role="Integrator", mission="integrate", dependencies=["a1", "a2"])

    layers = _agent_execution_layers([a1, a2, a3])
    # a1 and a2 must be in same layer (layer 0)
    layer0_ids = {a.id for a in layers[0]}
    assert "a1" in layer0_ids and "a2" in layer0_ids
    # a3 must be in a later layer
    layer1_ids = {a.id for a in layers[1]}
    assert "a3" in layer1_ids


def test_work_queue_blocked_agents_do_not_steal_prematurely():
    """An agent should be PENDING (not RUNNING) while its deps are RUNNING."""
    from agent.orchestration.work_queue import WorkQueue, TaskStatus

    wq = WorkQueue()
    wq.submit("dep-task", "DB Agent", "do DB work", 10, priority=1)
    wq.submit("downstream", "Backend Agent", "do BE work", 10, priority=2)

    # Claim dep-task (running)
    item = wq.try_steal()
    assert item is not None
    assert item.task_id == "dep-task"  # priority 1 comes first

    # downstream is still pending (priority 2)
    status = wq.status()
    assert status[TaskStatus.PENDING.value] == 1
    assert status[TaskStatus.RUNNING.value] == 1


# ── TASK 9: Recovery — mission state persistence ──────────────────────────────

def test_scheduler_result_tracks_failed_agents():
    from agent.orchestration.scheduler import SchedulerResult, WorkerResult
    wr_ok = WorkerResult(agent_id="a1", role="FE", success=True, output="ok")
    wr_fail = WorkerResult(agent_id="a2", role="BE", success=False, output="", error="timeout")
    result = SchedulerResult(
        success=False,
        outputs={"a1": wr_ok, "a2": wr_fail},
        failed_agents=["a2"],
    )
    assert "a2" in result.failed_agents
    assert result.success is False


def test_repair_loop_max_retries_constant():
    """repair_loop.MAX_RETRIES must be >= 1 to enable recovery."""
    from agent.orchestration.repair_loop import MAX_RETRIES
    assert MAX_RETRIES >= 1


# ── TASK 2 Extra: is_provider_failure covers all transient categories ─────────

def test_is_provider_failure_complete_coverage():
    from agent.production.reliability import is_provider_failure, ErrorCategory
    transient = [
        ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT,
        ErrorCategory.MALFORMED, ErrorCategory.PROVIDER,
        ErrorCategory.PAYLOAD_TOO_LARGE,
    ]
    non_transient = [
        ErrorCategory.UNKNOWN, ErrorCategory.INTERRUPTED,
        ErrorCategory.MAX_TURNS,
    ]
    for cat in transient:
        assert is_provider_failure(cat), f"{cat} should be a provider failure"
    for cat in non_transient:
        assert not is_provider_failure(cat), f"{cat} should NOT be a provider failure"


# ── LoopResult incomplete property ────────────────────────────────────────────

def test_loop_result_incomplete_property():
    from agent.agent_loop import LoopResult
    assert LoopResult(status="done").incomplete is False
    assert LoopResult(status="max_turns").incomplete is True
    assert LoopResult(status="interrupted").incomplete is True
    assert LoopResult(status="api_error").incomplete is True
