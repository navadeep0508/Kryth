"""Tests for the anti-paralysis engine (all 9 phases)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest
from agent.anti_paralysis import (
    AntiParalysisState,
    MAX_ANALYSIS_STEPS,
    record_file_read,
    get_duplicate_read_warning,
    was_file_read,
    enter_impl_mode,
    is_impl_mode,
    impl_mode_nudge,
    freeze_investigation,
    is_investigation_frozen,
    get_root_cause,
    record_tool_call,
    get_analysis_ratio,
    set_impl_queue,
    mark_queue_analyzed,
    get_impl_queue,
    impl_queue_nudge,
    worker_plan_guard,
    cache_fact,
    get_cached_fact,
    record_completed_fix,
    get_session_summary,
    record_tests_passed,
    should_stop,
    stop_after_success_nudge,
    get_metrics,
    format_metrics,
    reset_for_session,
)


# ── Helper ────────────────────────────────────────────────────────────────────

def fresh_id():
    """Return a unique session ID for each test."""
    import random
    return random.randint(100000, 999999)


# ── Phase 1: Read Budget ──────────────────────────────────────────────────────

class TestReadBudget:
    def test_first_read_not_duplicate(self):
        sid = fresh_id()
        assert record_file_read(sid, "agent_loop.py") is False

    def test_second_read_is_duplicate(self):
        sid = fresh_id()
        record_file_read(sid, "llm.py")
        assert record_file_read(sid, "llm.py") is True

    def test_different_files_not_duplicate(self):
        sid = fresh_id()
        record_file_read(sid, "a.py")
        assert record_file_read(sid, "b.py") is False

    def test_was_file_read_false_initially(self):
        sid = fresh_id()
        assert was_file_read(sid, "new_file.py") is False

    def test_was_file_read_true_after_read(self):
        sid = fresh_id()
        record_file_read(sid, "target.py")
        assert was_file_read(sid, "target.py") is True

    def test_duplicate_read_warning_message(self):
        warning = get_duplicate_read_warning("scheduler.py")
        assert "scheduler.py" in warning
        assert "cached" in warning.lower() or "duplicate" in warning.lower()

    def test_metrics_track_duplicate_reads(self):
        sid = fresh_id()
        record_file_read(sid, "x.py")
        record_file_read(sid, "x.py")
        record_file_read(sid, "x.py")
        m = get_metrics(sid)
        assert m.duplicate_file_reads == 2  # 2nd and 3rd are duplicates

    def test_empty_path_ignored(self):
        sid = fresh_id()
        assert record_file_read(sid, "") is False
        assert record_file_read(sid, None) is False


# ── Phase 2: Implementation Mode ─────────────────────────────────────────────

class TestImplMode:
    def test_not_in_impl_mode_initially(self):
        sid = fresh_id()
        assert is_impl_mode(sid) is False

    def test_enter_impl_mode(self):
        sid = fresh_id()
        enter_impl_mode(sid, "bug found in scheduler")
        assert is_impl_mode(sid) is True

    def test_impl_mode_nudge_contains_rules(self):
        nudge = impl_mode_nudge("null pointer in loop")
        assert "NO repository scans" in nudge
        assert "NO architecture reviews" in nudge
        assert "edit" in nudge.lower() or "files" in nudge.lower()

    def test_impl_mode_nudge_includes_reason(self):
        nudge = impl_mode_nudge("off-by-one error on line 42")
        assert "off-by-one" in nudge


# ── Phase 3: Single Investigation ─────────────────────────────────────────────

class TestSingleInvestigation:
    def test_investigation_not_frozen_initially(self):
        sid = fresh_id()
        assert is_investigation_frozen(sid) is False

    def test_freeze_investigation(self):
        sid = fresh_id()
        freeze_investigation(sid, "missing None check in classify_error")
        assert is_investigation_frozen(sid) is True

    def test_freeze_sets_root_cause(self):
        sid = fresh_id()
        freeze_investigation(sid, "race condition in scheduler")
        assert "race condition" in get_root_cause(sid)

    def test_freeze_also_enters_impl_mode(self):
        sid = fresh_id()
        freeze_investigation(sid, "off-by-one bug")
        assert is_impl_mode(sid) is True


# ── Phase 4: Execution Budget ──────────────────────────────────────────────────

class TestExecutionBudget:
    def test_analysis_tools_counted(self):
        sid = fresh_id()
        record_tool_call(sid, "read_file", "medium")
        record_tool_call(sid, "grep", "medium")
        m = get_metrics(sid)
        assert m.analysis_steps == 2

    def test_impl_tools_counted_separately(self):
        sid = fresh_id()
        record_tool_call(sid, "write_file", "medium")
        record_tool_call(sid, "run_command", "medium")
        m = get_metrics(sid)
        assert m.impl_steps == 2
        assert m.analysis_steps == 0

    def test_budget_exhausted_triggers_nudge(self):
        sid = fresh_id()
        limit = MAX_ANALYSIS_STEPS["simple"]  # 3
        nudge = None
        for i in range(limit + 1):
            nudge = record_tool_call(sid, "read_file", "simple")
        assert nudge is not None
        assert "EXECUTION BUDGET" in nudge or "analysis" in nudge.lower()

    def test_no_nudge_within_budget(self):
        sid = fresh_id()
        limit = MAX_ANALYSIS_STEPS["medium"]  # 8
        for i in range(limit - 1):
            result = record_tool_call(sid, "grep", "medium")
            assert result is None

    def test_complex_task_higher_budget(self):
        assert MAX_ANALYSIS_STEPS["complex"] > MAX_ANALYSIS_STEPS["simple"]
        assert MAX_ANALYSIS_STEPS["complex"] > MAX_ANALYSIS_STEPS["medium"]

    def test_analysis_ratio_zero_when_no_tools(self):
        sid = fresh_id()
        assert get_analysis_ratio(sid) == 0.0

    def test_analysis_ratio_calculation(self):
        sid = fresh_id()
        record_tool_call(sid, "read_file", "medium")   # analysis
        record_tool_call(sid, "write_file", "medium")  # impl
        ratio = get_analysis_ratio(sid)
        assert abs(ratio - 0.5) < 0.01

    def test_analysis_ratio_goal_under_30_percent(self):
        """Goal: ratio < 0.3 — mostly implementation, not analysis."""
        sid = fresh_id()
        for _ in range(2):
            record_tool_call(sid, "read_file", "medium")
        for _ in range(8):
            record_tool_call(sid, "write_file", "medium")
        ratio = get_analysis_ratio(sid)
        assert ratio < 0.3, f"Expected ratio < 0.3, got {ratio}"


# ── Phase 5: Implementation Queue ─────────────────────────────────────────────

class TestImplQueue:
    def test_set_and_get_impl_queue(self):
        sid = fresh_id()
        tasks = ["Fix finish_reason", "Update scheduler", "Add tests"]
        set_impl_queue(sid, tasks)
        assert get_impl_queue(sid) == tasks

    def test_impl_queue_nudge_lists_tasks(self):
        tasks = ["Fix A", "Fix B", "Fix C"]
        nudge = impl_queue_nudge(tasks)
        assert "Fix A" in nudge
        assert "Fix B" in nudge
        assert "no further analysis" in nudge.lower() or "analysis complete" in nudge.lower()

    def test_mark_queue_analyzed(self):
        sid = fresh_id()
        set_impl_queue(sid, ["task1"])
        mark_queue_analyzed(sid)
        from agent.anti_paralysis import _state_for
        assert _state_for(sid).impl_queue_analyzed is True


# ── Phase 6: No Replanning ─────────────────────────────────────────────────────

class TestNoReplanning:
    def test_no_guard_without_plan(self):
        result = worker_plan_guard(has_mission_plan=False)
        assert result is None

    def test_guard_blocks_when_plan_exists(self):
        result = worker_plan_guard(has_mission_plan=True)
        assert result is not None
        assert "NO REPLANNING" in result or "plan" in result.lower()

    def test_guard_message_directs_to_execution(self):
        msg = worker_plan_guard(has_mission_plan=True)
        assert "implement" in msg.lower() or "execution" in msg.lower()


# ── Phase 7: Cached Knowledge ──────────────────────────────────────────────────

class TestCachedKnowledge:
    def test_cache_and_retrieve_fact(self):
        sid = fresh_id()
        cache_fact(sid, "bug_location", "scheduler.py:line 234")
        assert get_cached_fact(sid, "bug_location") == "scheduler.py:line 234"

    def test_missing_fact_returns_none(self):
        sid = fresh_id()
        assert get_cached_fact(sid, "nonexistent") is None

    def test_record_completed_fix(self):
        sid = fresh_id()
        record_completed_fix(sid, "Fixed finish_reason propagation")
        summary = get_session_summary(sid)
        assert "finish_reason" in summary

    def test_session_summary_includes_files(self):
        sid = fresh_id()
        record_file_read(sid, "agent_loop.py")
        record_file_read(sid, "llm.py")
        summary = get_session_summary(sid)
        assert "agent_loop.py" in summary or "Files read" in summary

    def test_session_summary_empty_initially(self):
        sid = fresh_id()
        summary = get_session_summary(sid)
        assert "no cached knowledge" in summary.lower() or isinstance(summary, str)


# ── Phase 8: Stop After Success ────────────────────────────────────────────────

class TestStopAfterSuccess:
    def test_no_stop_initially(self):
        sid = fresh_id()
        assert should_stop(sid) is False

    def test_no_stop_with_only_tests_passed(self):
        sid = fresh_id()
        record_tests_passed(sid)
        assert should_stop(sid) is False  # need files written too

    def test_no_stop_with_only_files_written(self):
        sid = fresh_id()
        record_tool_call(sid, "write_file", "medium")
        assert should_stop(sid) is False  # need tests passed too

    def test_stop_when_files_and_tests(self):
        sid = fresh_id()
        record_tool_call(sid, "write_file", "medium")
        record_tests_passed(sid)
        assert should_stop(sid) is True

    def test_stop_nudge_message(self):
        nudge = stop_after_success_nudge()
        assert "STOP" in nudge or "complete" in nudge.lower()
        assert "re-audit" in nudge.lower() or "re-review" in nudge.lower()


# ── Phase 9: Metrics ───────────────────────────────────────────────────────────

class TestMetrics:
    def test_metrics_initial_state(self):
        sid = fresh_id()
        m = get_metrics(sid)
        assert m.duplicate_file_reads == 0
        assert m.analysis_steps == 0
        assert m.impl_steps == 0
        assert m.analysis_to_edit_ratio == 0.0

    def test_format_metrics_string(self):
        sid = fresh_id()
        record_tool_call(sid, "read_file", "medium")
        record_tool_call(sid, "write_file", "medium")
        output = format_metrics(sid)
        assert "Analysis steps" in output or "analysis" in output.lower()
        assert "Impl steps" in output or "impl" in output.lower()
        assert "Ratio" in output or "ratio" in output.lower()

    def test_metrics_goal_indicator(self):
        """format_metrics shows ✓ when ratio < 0.3."""
        sid = fresh_id()
        for _ in range(2):
            record_tool_call(sid, "grep", "medium")
        for _ in range(10):
            record_tool_call(sid, "write_file", "medium")
        output = format_metrics(sid)
        assert "✓" in output  # ratio 2/12 ≈ 0.17 < 0.3


# ── Reset / isolation ─────────────────────────────────────────────────────────

class TestIsolation:
    def test_reset_clears_state(self):
        sid = fresh_id()
        record_file_read(sid, "some_file.py")
        enter_impl_mode(sid, "test")
        reset_for_session(sid)
        assert is_impl_mode(sid) is False
        assert was_file_read(sid, "some_file.py") is False

    def test_different_sessions_are_isolated(self):
        sid1 = fresh_id()
        sid2 = fresh_id()
        record_file_read(sid1, "shared.py")
        assert was_file_read(sid2, "shared.py") is False

    def test_thread_safety(self):
        """Concurrent updates from different threads should not corrupt state."""
        import threading
        sid = fresh_id()
        errors = []

        def worker(i):
            try:
                record_file_read(sid, f"file_{i}.py")
                record_tool_call(sid, "write_file", "medium")
                cache_fact(sid, f"fact_{i}", f"value_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        m = get_metrics(sid)
        assert m.impl_steps == 20


# ── V1.6 additions ─────────────────────────────────────────────────────────────

from agent.anti_paralysis import (
    record_search, record_timing, get_memory, MissionMemory,
    generate_report, MissionReport, format_mission_report,
    should_inject_impl_nudge,
)


class TestV16SearchTracking:
    def test_record_search_not_duplicate_first_time(self):
        sid = fresh_id()
        from agent.anti_paralysis import record_search
        assert record_search(sid, "def classify_error") is False

    def test_record_search_duplicate_second_time(self):
        sid = fresh_id()
        record_search(sid, "same query")
        assert record_search(sid, "same query") is True

    def test_record_search_tracks_count(self):
        sid = fresh_id()
        record_search(sid, "query1")
        record_search(sid, "query2")
        record_search(sid, "query1")  # duplicate
        m = get_metrics(sid)
        assert m.duplicate_searches == 1

    def test_empty_query_not_tracked(self):
        sid = fresh_id()
        assert record_search(sid, "") is False
        assert record_search(sid, None) is False


class TestV16Timing:
    def test_record_timing_analysis_tool(self):
        sid = fresh_id()
        record_timing(sid, "read_file", 0.5)
        from agent.anti_paralysis import _state_for
        assert _state_for(sid).analysis_time_s == pytest.approx(0.5)

    def test_record_timing_impl_tool(self):
        sid = fresh_id()
        record_timing(sid, "write_file", 1.2)
        from agent.anti_paralysis import _state_for
        assert _state_for(sid).impl_time_s == pytest.approx(1.2)

    def test_record_timing_accumulates(self):
        sid = fresh_id()
        record_timing(sid, "grep", 0.3)
        record_timing(sid, "grep", 0.2)
        from agent.anti_paralysis import _state_for
        assert _state_for(sid).analysis_time_s == pytest.approx(0.5)

    def test_record_timing_ignores_zero(self):
        sid = fresh_id()
        record_timing(sid, "read_file", 0.0)
        from agent.anti_paralysis import _state_for
        assert _state_for(sid).analysis_time_s == 0.0


class TestV16MissionMemory:
    def test_get_memory_returns_instance(self):
        sid = fresh_id()
        mem = get_memory(sid)
        assert isinstance(mem, MissionMemory)

    def test_same_session_same_instance(self):
        sid = fresh_id()
        m1 = get_memory(sid)
        m2 = get_memory(sid)
        assert m1 is m2

    def test_remember_and_recall_file(self):
        sid = fresh_id()
        mem = get_memory(sid)
        mem.remember_file("scheduler.py", "Contains retry logic")
        recalled = mem.recall_file("scheduler.py")
        assert recalled is not None
        assert "retry" in recalled.lower()

    def test_recall_unknown_file_returns_none(self):
        sid = fresh_id()
        mem = get_memory(sid)
        assert mem.recall_file("unknown.py") is None

    def test_should_skip_read_known_file(self):
        sid = fresh_id()
        mem = get_memory(sid)
        mem.remember_file("agent_loop.py", "Root loop")
        assert mem.should_skip_read("agent_loop.py") is True

    def test_should_not_skip_unknown_file(self):
        sid = fresh_id()
        mem = get_memory(sid)
        assert mem.should_skip_read("new_file.py") is False

    def test_remember_root_cause(self):
        sid = fresh_id()
        mem = get_memory(sid)
        mem.remember_root_cause("off-by-one in scheduler")
        causes = mem.get_root_causes()
        assert any("off-by-one" in c for c in causes)

    def test_to_prompt_block_empty_when_no_memory(self):
        sid = fresh_id()
        mem = get_memory(sid)
        block = mem.to_prompt_block()
        assert block == "" or "MISSION MEMORY" in block

    def test_to_prompt_block_contains_files(self):
        sid = fresh_id()
        mem = get_memory(sid)
        mem.remember_file("reliability.py", "error classification")
        block = mem.to_prompt_block()
        assert "reliability.py" in block

    def test_to_prompt_block_contains_root_causes(self):
        sid = fresh_id()
        mem = get_memory(sid)
        mem.remember_root_cause("thread safety bug")
        block = mem.to_prompt_block()
        assert "thread safety" in block


class TestV16MissionReport:
    def test_generate_report_returns_mission_report(self):
        sid = fresh_id()
        report = generate_report(sid)
        assert isinstance(report, MissionReport)

    def test_generate_report_fields_populated(self):
        sid = fresh_id()
        record_file_read(sid, "a.py")
        record_file_read(sid, "a.py")  # duplicate
        record_search(sid, "pattern")
        record_search(sid, "pattern")  # duplicate
        record_tool_call(sid, "read_file", "medium")
        record_tool_call(sid, "write_file", "medium")
        record_timing(sid, "read_file", 0.5)
        record_timing(sid, "write_file", 1.0)

        r = generate_report(sid)
        assert r.total_file_reads >= 2
        assert r.duplicate_file_reads == 1
        assert r.total_searches >= 2
        assert r.duplicate_searches == 1
        assert r.analysis_steps >= 1
        assert r.impl_steps >= 1
        assert r.analysis_time_s == pytest.approx(0.5)
        assert r.impl_time_s == pytest.approx(1.0)

    def test_format_mission_report_shows_metrics(self):
        sid = fresh_id()
        record_tool_call(sid, "read_file", "medium")
        record_tool_call(sid, "write_file", "medium")
        output = format_mission_report(sid)
        assert "Analysis steps" in output
        assert "Impl steps" in output
        assert "Mission duration" in output


class TestV16ImplModeNudge:
    def test_no_nudge_when_tool_calls_present(self):
        sid = fresh_id()
        enter_impl_mode(sid, "test")
        assert should_inject_impl_nudge(sid, had_tool_calls=True) is False

    def test_nudge_when_impl_mode_and_no_tools(self):
        sid = fresh_id()
        enter_impl_mode(sid, "test")
        assert should_inject_impl_nudge(sid, had_tool_calls=False) is True

    def test_nudge_limited_to_two_injections(self):
        sid = fresh_id()
        enter_impl_mode(sid, "test")
        assert should_inject_impl_nudge(sid, had_tool_calls=False) is True
        assert should_inject_impl_nudge(sid, had_tool_calls=False) is True
        # Third time must NOT inject (spam prevention)
        assert should_inject_impl_nudge(sid, had_tool_calls=False) is False

    def test_no_nudge_when_not_in_impl_mode(self):
        sid = fresh_id()
        assert should_inject_impl_nudge(sid, had_tool_calls=False) is False
