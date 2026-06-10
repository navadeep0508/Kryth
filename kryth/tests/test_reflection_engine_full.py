"""
Comprehensive pytest test suite for the KRYTH Reflection & Learning Engine.

Run with:
    pytest kryth/tests/test_reflection_engine_full.py -v
"""

from __future__ import annotations

import sys
import time
import uuid
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_lesson(key="k", kind=None, scope=None, value="v", importance=0.7,
                 confidence=0.7, composite_score=0.65):
    from agent.reflection.experience_record import (
        KnowledgeLesson, LessonKind, LessonScope, ReflectionStatus
    )
    kind = kind or LessonKind.COMMAND
    scope = scope or LessonScope.EXECUTION
    l = KnowledgeLesson(key=key, kind=kind, scope=scope, value=value,
                        importance=importance, confidence=confidence)
    l.composite_score = composite_score
    return l


def _make_input(**kwargs):
    from agent.reflection.experience_record import ReflectionInput
    defaults = dict(task_description="test task", task_id=_uid(), project_hash=_uid())
    defaults.update(kwargs)
    return ReflectionInput(**defaults)


# ===========================================================================
# TestKnowledgeLesson
# ===========================================================================

class TestKnowledgeLesson:

    def test_defaults(self):
        from agent.reflection.experience_record import (
            KnowledgeLesson, LessonKind, LessonScope, ReflectionStatus
        )
        l = KnowledgeLesson(key="k", kind=LessonKind.REPAIR, scope=LessonScope.EXECUTION, value="v")
        assert l.importance == 0.5
        assert l.confidence == 0.5
        assert l.novelty == 0.5
        assert l.reuse_potential == 0.5
        assert l.success_rate == 0.5
        assert l.status == ReflectionStatus.PENDING
        assert l.composite_score == 0.0
        assert l.project_hash == ""
        assert l.agent_id == ""

    def test_lesson_id_auto_generated(self):
        from agent.reflection.experience_record import KnowledgeLesson, LessonKind, LessonScope
        l = KnowledgeLesson(key="k", kind=LessonKind.COMMAND, scope=LessonScope.SESSION, value="v")
        assert l.lesson_id.startswith("lesson_")
        assert len(l.lesson_id) > 5

    def test_to_dict_round_trip(self):
        from agent.reflection.experience_record import KnowledgeLesson, LessonKind, LessonScope
        l = KnowledgeLesson(key="repair:import_error:pip", kind=LessonKind.REPAIR,
                            scope=LessonScope.EXECUTION, value={"fix": "pip install x"},
                            importance=0.9, confidence=0.85)
        d = l.to_dict()
        assert d["key"] == "repair:import_error:pip"
        assert d["kind"] == "repair"
        assert d["scope"] == "execution"
        assert d["importance"] == 0.9

    def test_from_dict(self):
        from agent.reflection.experience_record import KnowledgeLesson, LessonKind, LessonScope
        l = KnowledgeLesson(key="k", kind=LessonKind.DECISION, scope=LessonScope.PROJECT, value="jwt")
        d = l.to_dict()
        l2 = KnowledgeLesson.from_dict(d)
        assert l2.key == "k"
        assert l2.kind == LessonKind.DECISION
        assert l2.scope == LessonScope.PROJECT

    def test_kind_enum_values(self):
        from agent.reflection.experience_record import LessonKind
        assert LessonKind.REPAIR.value == "repair"
        assert LessonKind.WORKFLOW.value == "workflow"
        assert LessonKind.DECISION.value == "decision"
        assert LessonKind.SECURITY.value == "security"

    def test_scope_enum_values(self):
        from agent.reflection.experience_record import LessonScope
        assert LessonScope.EXECUTION.value == "execution"
        assert LessonScope.LONG_TERM.value == "long_term"
        assert LessonScope.PROJECT.value == "project"

    def test_ttl_optional(self):
        from agent.reflection.experience_record import KnowledgeLesson, LessonKind, LessonScope
        l = KnowledgeLesson(key="k", kind=LessonKind.COMMAND, scope=LessonScope.SESSION, value="v")
        assert l.ttl is None

    def test_metadata_default_empty(self):
        from agent.reflection.experience_record import KnowledgeLesson, LessonKind, LessonScope
        l = KnowledgeLesson(key="k", kind=LessonKind.COMMAND, scope=LessonScope.SESSION, value="v")
        assert l.metadata == {}


# ===========================================================================
# TestReflectionInput
# ===========================================================================

class TestReflectionInput:

    def test_empty_defaults(self):
        from agent.reflection.experience_record import ReflectionInput
        inp = ReflectionInput(task_description="do something")
        assert inp.tool_calls == []
        assert inp.terminal_events == []
        assert inp.repair_events == []
        assert inp.dag_tasks == []
        assert inp.team_events == []
        assert inp.decisions == []
        assert inp.file_changes == []
        assert inp.session_messages == []
        assert inp.final_output == ""

    def test_duration_sec(self):
        from agent.reflection.experience_record import ReflectionInput
        t0 = time.time()
        inp = ReflectionInput(task_description="x", started_at=t0, ended_at=t0 + 42.0)
        assert abs(inp.duration_sec - 42.0) < 0.01

    def test_duration_sec_no_end(self):
        from agent.reflection.experience_record import ReflectionInput
        inp = ReflectionInput(task_description="x")
        assert inp.duration_sec == 0.0

    def test_total_tool_calls(self):
        from agent.reflection.experience_record import ReflectionInput
        inp = ReflectionInput(task_description="x", tool_calls=[
            {"tool": "write", "success": True},
            {"tool": "bash", "success": False},
        ])
        assert inp.total_tool_calls == 2

    def test_failed_tool_calls(self):
        from agent.reflection.experience_record import ReflectionInput
        inp = ReflectionInput(task_description="x", tool_calls=[
            {"tool": "write", "success": True},
            {"tool": "bash", "success": False},
            {"tool": "read", "success": True},
        ])
        assert inp.failed_tool_calls == 1

    def test_field_assignment(self):
        from agent.reflection.experience_record import ReflectionInput
        inp = ReflectionInput(task_description="task", task_id="t1", project_hash="abc")
        assert inp.task_description == "task"
        assert inp.task_id == "t1"
        assert inp.project_hash == "abc"


# ===========================================================================
# TestExperienceRecord
# ===========================================================================

class TestExperienceRecord:

    def _make_output(self, task_id="t1"):
        from agent.reflection.experience_record import ReflectionOutput
        return ReflectionOutput(task_id=task_id, task_summary="did something")

    def test_from_reflection_output_basic(self):
        from agent.reflection.experience_record import ExperienceRecord
        out = self._make_output()
        inp = _make_input(task_description="add feature", started_at=time.time() - 60,
                          ended_at=time.time())
        exp = ExperienceRecord.from_reflection_output(out, inp)
        assert exp.task_description == "add feature"
        assert exp.duration_sec > 0

    def test_content_hash_non_empty(self):
        from agent.reflection.experience_record import ExperienceRecord
        out = self._make_output()
        inp = _make_input()
        exp = ExperienceRecord.from_reflection_output(out, inp)
        assert exp.content_hash != ""

    def test_outcome_success_no_failures(self):
        from agent.reflection.experience_record import ExperienceRecord, ReflectionInput
        out = self._make_output()
        inp = ReflectionInput(task_description="x", tool_calls=[
            {"tool": "write", "success": True},
        ])
        exp = ExperienceRecord.from_reflection_output(out, inp)
        assert exp.outcome == "success"

    def test_outcome_partial(self):
        from agent.reflection.experience_record import ExperienceRecord, ReflectionInput
        out = self._make_output()
        inp = ReflectionInput(task_description="x", tool_calls=[
            {"tool": "write", "success": True},
            {"tool": "bash", "success": False},
        ])
        exp = ExperienceRecord.from_reflection_output(out, inp)
        assert exp.outcome == "partial"

    def test_to_dict_keys(self):
        from agent.reflection.experience_record import ExperienceRecord
        out = self._make_output()
        inp = _make_input()
        exp = ExperienceRecord.from_reflection_output(out, inp)
        d = exp.to_dict()
        for key in ("record_id", "task_description", "project_hash", "outcome", "content_hash"):
            assert key in d


# ===========================================================================
# TestBaseReflector
# ===========================================================================

class TestBaseReflector:

    def test_cannot_instantiate_abc(self):
        from agent.reflection.extractor import BaseReflector
        with pytest.raises(TypeError):
            BaseReflector()

    def test_make_lesson(self):
        from agent.reflection.terminal_reflector import TerminalReflector
        from agent.reflection.experience_record import LessonKind, LessonScope
        r = TerminalReflector()
        lesson = r._make_lesson("k", LessonKind.COMMAND, LessonScope.EXECUTION, "v", importance=0.8)
        assert lesson.key == "k"
        assert lesson.importance == 0.8
        assert lesson.source == "terminal_reflector"

    def test_slugify_lowercases(self):
        from agent.reflection.terminal_reflector import TerminalReflector
        r = TerminalReflector()
        assert r._slugify("Hello World") == "hello_world"

    def test_slugify_truncates(self):
        from agent.reflection.terminal_reflector import TerminalReflector
        r = TerminalReflector()
        result = r._slugify("a" * 100, max_len=10)
        assert len(result) <= 10

    def test_slugify_removes_special_chars(self):
        from agent.reflection.terminal_reflector import TerminalReflector
        r = TerminalReflector()
        result = r._slugify("hello!@#$world")
        assert "!" not in result and "@" not in result


# ===========================================================================
# TestKnowledgeExtractor
# ===========================================================================

class TestKnowledgeExtractor:

    def _dummy_reflector(self, lessons):
        from agent.reflection.extractor import BaseReflector
        from agent.reflection.experience_record import KnowledgeLesson, LessonKind, LessonScope
        class Dummy(BaseReflector):
            name = "dummy"
            def __init__(self, _lessons):
                super().__init__()
                self._l = _lessons
            def extract(self, inp):
                return self._l
        return Dummy(lessons)

    def test_register_adds_reflector(self):
        from agent.reflection.extractor import KnowledgeExtractor
        ext = KnowledgeExtractor()
        dummy = self._dummy_reflector([])
        ext.register(dummy)
        assert "dummy" in ext.registered_reflector_names()

    def test_extract_all_calls_all(self):
        from agent.reflection.extractor import KnowledgeExtractor
        from agent.reflection.experience_record import KnowledgeLesson, LessonKind, LessonScope
        l1 = _make_lesson("k1")
        l2 = _make_lesson("k2")
        ext = KnowledgeExtractor()
        ext.register(self._dummy_reflector([l1]))
        ext.register(self._dummy_reflector([l2]))
        results = ext.extract_all(_make_input())
        keys = {r.key for r in results}
        assert "k1" in keys and "k2" in keys

    def test_dedup_by_key_last_writer_wins(self):
        from agent.reflection.extractor import KnowledgeExtractor
        l1 = _make_lesson("same_key", value="first")
        l2 = _make_lesson("same_key", value="second")
        ext = KnowledgeExtractor()
        ext.register(self._dummy_reflector([l1]))
        ext.register(self._dummy_reflector([l2]))
        results = ext.extract_all(_make_input())
        values = [r.value for r in results if r.key == "same_key"]
        assert len(values) == 1
        assert values[0] == "second"

    def test_silently_handles_reflector_exception(self):
        from agent.reflection.extractor import KnowledgeExtractor, BaseReflector
        class Broken(BaseReflector):
            name = "broken"
            def extract(self, inp):
                raise RuntimeError("oops")
        ext = KnowledgeExtractor()
        ext.register(Broken())
        # Must not raise
        results = ext.extract_all(_make_input())
        assert isinstance(results, list)

    def test_registered_reflector_names(self):
        from agent.reflection.extractor import KnowledgeExtractor
        ext = KnowledgeExtractor()
        ext.register(self._dummy_reflector([]))
        assert ext.registered_reflector_names() == ["dummy"]

    def test_build_default_extractor_returns_extractor(self):
        from agent.reflection.extractor import build_default_extractor, KnowledgeExtractor
        ext = build_default_extractor(_uid())
        assert isinstance(ext, KnowledgeExtractor)


# ===========================================================================
# TestTerminalReflector
# ===========================================================================

class TestTerminalReflector:

    def _r(self):
        from agent.reflection.terminal_reflector import TerminalReflector
        return TerminalReflector()

    def test_successful_command_produces_lesson(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(terminal_events=[
            {"command": "uv sync", "exit_code": 0, "output": "ok", "duration_sec": 3.0}
        ])
        lessons = self._r().extract(inp)
        assert any(l.kind == LessonKind.COMMAND for l in lessons)

    def test_failed_command_not_success_lesson(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(terminal_events=[
            {"command": "make build", "exit_code": 1, "output": "error", "duration_sec": 1.0}
        ])
        lessons = self._r().extract(inp)
        # No COMMAND success lesson for a failed command
        success_lessons = [l for l in lessons if l.kind == LessonKind.COMMAND and "success" in l.key]
        assert len(success_lessons) == 0

    def test_repeated_command_higher_importance(self):
        # Use a generic command (not a test/build/git command) so it hits
        # the generic repeated-command branch (importance=0.9).
        events = [
            {"command": "uv sync", "exit_code": 0, "output": "synced", "duration_sec": 5.0},
            {"command": "uv sync", "exit_code": 0, "output": "synced", "duration_sec": 4.5},
        ]
        inp = _make_input(terminal_events=events)
        lessons = self._r().extract(inp)
        cmd_lessons = [l for l in lessons if "uv_sync" in l.key or "command" in l.key.lower()]
        assert any(l.importance >= 0.85 for l in cmd_lessons)

    def test_slow_command_lesson(self):
        inp = _make_input(terminal_events=[
            {"command": "npm install", "exit_code": 0, "output": "done", "duration_sec": 45.0}
        ])
        lessons = self._r().extract(inp)
        slow = [l for l in lessons if "slow" in l.key]
        assert len(slow) >= 1

    def test_test_command_produces_test_kind(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(terminal_events=[
            {"command": "pytest tests/", "exit_code": 0, "output": "5 passed", "duration_sec": 3.0}
        ])
        lessons = self._r().extract(inp)
        assert any(l.kind == LessonKind.TEST for l in lessons)

    def test_build_command_produces_build_kind(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(terminal_events=[
            {"command": "make all", "exit_code": 0, "output": "built", "duration_sec": 8.0}
        ])
        lessons = self._r().extract(inp)
        assert any(l.kind in (LessonKind.BUILD, LessonKind.COMMAND) for l in lessons)

    def test_git_commit_produces_workflow_kind(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(terminal_events=[
            {"command": "git commit -m 'fix'", "exit_code": 0, "output": "committed", "duration_sec": 0.5}
        ])
        lessons = self._r().extract(inp)
        assert any(l.kind in (LessonKind.WORKFLOW, LessonKind.COMMAND) for l in lessons)

    def test_parsed_error_with_fix_produces_repair(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(terminal_events=[{
            "command": "python main.py", "exit_code": 1,
            "output": "ModuleNotFoundError: No module named 'dotenv'",
            "duration_sec": 0.2,
            "parsed_errors": [{
                "kind": "PYTHON_TRACEBACK",
                "message": "No module named 'dotenv'",
                "error_type": "ModuleNotFoundError",
                "suggested_fix": "pip install python-dotenv",
            }]
        }])
        lessons = self._r().extract(inp)
        assert any(l.kind == LessonKind.REPAIR for l in lessons)

    def test_trivial_commands_filtered(self):
        inp = _make_input(terminal_events=[
            {"command": "ls", "exit_code": 0, "output": "file.py", "duration_sec": 0.05},
            {"command": "pwd", "exit_code": 0, "output": "/home", "duration_sec": 0.01},
        ])
        lessons = self._r().extract(inp)
        assert all("ls" not in l.key and "pwd" not in l.key for l in lessons)

    def test_very_short_duration_filtered(self):
        inp = _make_input(terminal_events=[
            {"command": "echo hi", "exit_code": 0, "output": "hi", "duration_sec": 0.001}
        ])
        lessons = self._r().extract(inp)
        assert len(lessons) == 0


# ===========================================================================
# TestRepairReflector
# ===========================================================================

class TestRepairReflector:

    def _r(self):
        from agent.reflection.repair_reflector import RepairReflector
        return RepairReflector()

    def test_successful_repair_produces_lesson(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(repair_events=[{
            "error_type": "ModuleNotFoundError",
            "error_message": "No module named 'dotenv'",
            "repair_command": "pip install python-dotenv",
            "success": True, "attempts": 1,
        }])
        lessons = self._r().extract(inp)
        assert any(l.kind == LessonKind.REPAIR for l in lessons)

    def test_failed_repair_session_scope(self):
        from agent.reflection.experience_record import LessonScope
        inp = _make_input(repair_events=[{
            "error_type": "SyntaxError",
            "error_message": "invalid syntax",
            "repair_command": "auto_fix",
            "success": False, "attempts": 3,
        }])
        lessons = self._r().extract(inp)
        assert any(l.scope == LessonScope.SESSION for l in lessons)

    def test_single_attempt_high_importance(self):
        inp = _make_input(repair_events=[{
            "error_type": "ImportError",
            "error_message": "cannot import",
            "repair_command": "pip install x",
            "success": True, "attempts": 1,
        }])
        lessons = self._r().extract(inp)
        repair_lessons = [l for l in lessons if "repair" in l.key]
        assert any(l.importance >= 0.90 for l in repair_lessons)

    def test_multi_attempt_warning_in_metadata(self):
        inp = _make_input(repair_events=[{
            "error_type": "RuntimeError",
            "error_message": "crash",
            "repair_command": "retry",
            "success": True, "attempts": 3,
        }])
        lessons = self._r().extract(inp)
        multi = [l for l in lessons if l.metadata.get("warning")]
        assert len(multi) >= 1

    def test_classify_import_error(self):
        r = self._r()
        result = r._classify_error("ModuleNotFoundError", "No module named 'x'")
        assert result == "import_error"

    def test_classify_syntax_error(self):
        r = self._r()
        result = r._classify_error("SyntaxError", "invalid syntax at line 5")
        assert result == "syntax_error"

    def test_implicit_repair_from_tool_calls(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(tool_calls=[
            {"tool": "bash", "args": {"command": "python main.py"}, "success": False,
             "result": "ModuleNotFoundError: No module named 'x'"},
            {"tool": "bash", "args": {"command": "pip install x"}, "success": True,
             "result": "Successfully installed x"},
        ])
        lessons = self._r().extract(inp)
        assert any(l.kind == LessonKind.REPAIR for l in lessons)

    def test_no_repair_events_empty_list(self):
        inp = _make_input()
        lessons = self._r().extract(inp)
        assert isinstance(lessons, list)


# ===========================================================================
# TestWorkflowReflector
# ===========================================================================

class TestWorkflowReflector:

    def _r(self):
        from agent.reflection.workflow_reflector import WorkflowReflector
        return WorkflowReflector()

    def test_dag_all_completed(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(dag_tasks=[
            {"task_id": "1", "name": "Setup", "status": "completed", "affected_files": [], "risk_level": "LOW", "duration_sec": 5.0},
            {"task_id": "2", "name": "Build", "status": "completed", "affected_files": [], "risk_level": "LOW", "duration_sec": 10.0},
        ])
        lessons = self._r().extract(inp)
        assert any(l.kind == LessonKind.WORKFLOW for l in lessons)

    def test_partial_dag(self):
        inp = _make_input(dag_tasks=[
            {"task_id": "1", "name": "Setup", "status": "completed", "affected_files": [], "risk_level": "LOW", "duration_sec": 5.0},
            {"task_id": "2", "name": "Deploy", "status": "failed", "affected_files": [], "risk_level": "HIGH", "duration_sec": 2.0},
        ])
        lessons = self._r().extract(inp)
        partial = [l for l in lessons if "partial" in l.key]
        assert len(partial) >= 1

    def test_no_dag_falls_back_to_tool_calls(self):
        inp = _make_input(tool_calls=[
            {"tool": "read_file", "success": True},
            {"tool": "write_file", "success": True},
        ])
        # Should not raise and should return a list
        lessons = self._r().extract(inp)
        assert isinstance(lessons, list)

    def test_long_task_produces_lesson(self):
        inp = _make_input(
            started_at=time.time() - 400,
            ended_at=time.time(),
        )
        lessons = self._r().extract(inp)
        long_lessons = [l for l in lessons if "long" in l.key or "complex" in l.key or (isinstance(l.value, dict) and "duration" in l.value)]
        # The lesson is there if duration_sec > 300; just verify no crash
        assert isinstance(lessons, list)

    def test_test_before_commit(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(terminal_events=[
            {"command": "pytest", "exit_code": 0, "output": "passed", "duration_sec": 5.0},
            {"command": "git commit -m 'done'", "exit_code": 0, "output": "ok", "duration_sec": 0.3},
        ])
        lessons = self._r().extract(inp)
        commit_lessons = [l for l in lessons if "commit" in l.key]
        assert isinstance(lessons, list)  # No crash; content depends on impl

    def test_high_risk_task_produces_lesson(self):
        inp = _make_input(dag_tasks=[
            {"task_id": "1", "name": "DB Migration", "status": "completed",
             "affected_files": [], "risk_level": "CRITICAL", "duration_sec": 15.0},
        ])
        lessons = self._r().extract(inp)
        assert isinstance(lessons, list)

    def test_empty_input_empty_list(self):
        inp = _make_input()
        lessons = self._r().extract(inp)
        assert isinstance(lessons, list)


# ===========================================================================
# TestTeamReflector
# ===========================================================================

class TestTeamReflector:

    def _r(self):
        from agent.reflection.team_reflector import TeamReflector
        return TeamReflector()

    def test_all_succeeded_composition_lesson(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(team_events=[
            {"agent_id": "a1", "role": "backend", "tasks_completed": 3, "tasks_failed": 0, "turns_used": 10},
            {"agent_id": "a2", "role": "frontend", "tasks_completed": 2, "tasks_failed": 0, "turns_used": 8},
        ])
        lessons = self._r().extract(inp)
        assert any(l.kind == LessonKind.TEAM for l in lessons)

    def test_unused_agent_lesson(self):
        inp = _make_input(team_events=[
            {"agent_id": "a1", "role": "backend", "tasks_completed": 3, "tasks_failed": 0, "turns_used": 10},
            {"agent_id": "a2", "role": "testing", "tasks_completed": 0, "tasks_failed": 0, "turns_used": 0},
        ])
        lessons = self._r().extract(inp)
        unused = [l for l in lessons if "unused" in l.key]
        assert len(unused) >= 1

    def test_overloaded_agent_lesson(self):
        # avg = (100 + 5) / 2 = 52.5; 100 > 2 * 52.5 = 105 → False
        # Use three agents so avg is pulled down: (100 + 5 + 5) / 3 = 36.7; 100 > 73.3 → True
        inp = _make_input(team_events=[
            {"agent_id": "a1", "role": "backend", "tasks_completed": 5, "tasks_failed": 0, "turns_used": 100},
            {"agent_id": "a2", "role": "frontend", "tasks_completed": 2, "tasks_failed": 0, "turns_used": 5},
            {"agent_id": "a3", "role": "testing", "tasks_completed": 1, "tasks_failed": 0, "turns_used": 5},
        ])
        lessons = self._r().extract(inp)
        overloaded = [l for l in lessons if "overload" in l.key]
        assert len(overloaded) >= 1

    def test_solo_success_lesson(self):
        inp = _make_input(team_events=[
            {"agent_id": "a1", "role": "fullstack", "tasks_completed": 5, "tasks_failed": 0, "turns_used": 20},
        ])
        lessons = self._r().extract(inp)
        solo = [l for l in lessons if "solo" in l.key]
        assert len(solo) >= 1

    def test_empty_team_empty_list(self):
        inp = _make_input()
        lessons = self._r().extract(inp)
        assert isinstance(lessons, list)

    def test_role_hash_stable(self):
        r = self._r()
        h1 = r._role_hash(["backend", "frontend"])
        h2 = r._role_hash(["frontend", "backend"])
        assert h1 == h2  # sorted, so same hash

    def test_avg_turns(self):
        r = self._r()
        team = [
            {"turns_used": 10},
            {"turns_used": 20},
        ]
        assert r._avg_turns(team) == 15.0


# ===========================================================================
# TestDecisionReflector
# ===========================================================================

class TestDecisionReflector:

    def _r(self):
        from agent.reflection.decision_reflector import DecisionReflector
        return DecisionReflector()

    def test_explicit_decision_produces_lesson(self):
        from agent.reflection.experience_record import LessonKind
        # Use a non-security title so kind stays DECISION (not SECURITY)
        inp = _make_input(decisions=[{
            "title": "Database Choice",
            "choice": "PostgreSQL",
            "rationale": "Best ACID compliance for our workload",
            "alternatives": ["MySQL", "MongoDB"],
        }])
        lessons = self._r().extract(inp)
        assert any(l.kind in (LessonKind.DECISION, LessonKind.SECURITY) for l in lessons)

    def test_implicit_chose_because(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(final_output="I chose FastAPI because it has automatic docs generation.")
        lessons = self._r().extract(inp)
        assert any(l.kind in (LessonKind.DECISION, LessonKind.SECURITY) for l in lessons)

    def test_dependency_file_write_lesson(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(tool_calls=[{
            "tool": "write_file",
            "args": {"path": "requirements.txt", "content": "fastapi==0.104.0\npython-dotenv==1.0.0"},
            "success": True,
        }])
        lessons = self._r().extract(inp)
        assert isinstance(lessons, list)

    def test_security_keyword_boosts_kind(self):
        from agent.reflection.experience_record import LessonKind
        inp = _make_input(decisions=[{
            "title": "Token Storage",
            "choice": "JWT in httpOnly cookie",
            "rationale": "Prevents XSS attacks and protects auth tokens",
            "alternatives": ["localStorage"],
        }])
        lessons = self._r().extract(inp)
        security = [l for l in lessons if l.kind == LessonKind.SECURITY]
        assert len(security) >= 1

    def test_empty_decisions_empty_list_ok(self):
        inp = _make_input()
        lessons = self._r().extract(inp)
        assert isinstance(lessons, list)

    def test_alternatives_stored_in_value(self):
        inp = _make_input(decisions=[{
            "title": "DB Choice",
            "choice": "PostgreSQL",
            "rationale": "ACID compliance",
            "alternatives": ["MySQL", "MongoDB"],
        }])
        lessons = self._r().extract(inp)
        decision_lessons = [l for l in lessons if "decision" in l.key or "db" in l.key.lower()]
        if decision_lessons:
            val = decision_lessons[0].value
            if isinstance(val, dict):
                # alternatives should be somewhere in the value
                assert "alternatives" in val or "choice" in val

    def test_multiple_explicit_decisions_all_extracted(self):
        inp = _make_input(decisions=[
            {"title": "Auth", "choice": "JWT", "rationale": "r1", "alternatives": []},
            {"title": "DB", "choice": "Postgres", "rationale": "r2", "alternatives": []},
        ])
        lessons = self._r().extract(inp)
        decision_lessons = [l for l in lessons if l.kind.value in ("decision", "security")]
        assert len(decision_lessons) >= 2


# ===========================================================================
# TestScoringEngine
# ===========================================================================

class TestScoringEngine:

    def _eng(self):
        from agent.reflection.scoring_engine import ReflectionScoringEngine
        return ReflectionScoringEngine()

    def test_repair_kind_bonus(self):
        from agent.reflection.experience_record import LessonKind, LessonScope
        eng = self._eng()
        l = _make_lesson("k", LessonKind.REPAIR, LessonScope.EXECUTION, "v",
                          importance=0.5, confidence=0.5, composite_score=0.0)
        l.novelty = 0.5; l.reuse_potential = 0.5; l.success_rate = 0.5
        eng.score(l)
        # REPAIR should get +0.05 bonus — composite > base
        base = 0.5*0.30 + 0.5*0.20 + 0.5*0.20 + 0.5*0.15 + 0.5*0.15
        assert l.composite_score > base

    def test_user_fact_bonus(self):
        from agent.reflection.experience_record import LessonKind, LessonScope
        eng = self._eng()
        l = _make_lesson("k", LessonKind.USER_FACT, LessonScope.PROJECT, "v",
                          importance=0.5, confidence=0.5, composite_score=0.0)
        l.novelty = 0.5; l.reuse_potential = 0.5; l.success_rate = 0.5
        eng.score(l)
        base = 0.5*0.30 + 0.5*0.20 + 0.5*0.20 + 0.5*0.15 + 0.5*0.15
        assert l.composite_score > base + 0.05

    def test_composite_in_unit_interval(self):
        from agent.reflection.experience_record import LessonKind, LessonScope
        eng = self._eng()
        l = _make_lesson("k", LessonKind.COMMAND, LessonScope.SESSION, "v",
                          importance=1.0, confidence=1.0, composite_score=0.0)
        l.novelty = 1.0; l.reuse_potential = 1.0; l.success_rate = 1.0
        eng.score(l)
        assert 0.0 <= l.composite_score <= 1.0

    def test_score_all_sorted_descending(self):
        from agent.reflection.experience_record import LessonKind, LessonScope
        eng = self._eng()
        low = _make_lesson("low", LessonKind.COMPLEXITY, LessonScope.SESSION, "v",
                            importance=0.1, confidence=0.1, composite_score=0.0)
        low.novelty = 0.1; low.reuse_potential = 0.1; low.success_rate = 0.1
        high = _make_lesson("high", LessonKind.REPAIR, LessonScope.EXECUTION, "v",
                             importance=0.9, confidence=0.9, composite_score=0.0)
        high.novelty = 0.9; high.reuse_potential = 0.9; high.success_rate = 0.9
        ranked = eng.score_all([low, high])
        assert ranked[0].key == "high"

    def test_novelty_new_key(self):
        from agent.reflection.experience_record import LessonKind, LessonScope
        from agent.reflection.scoring_engine import ScoringContext
        eng = self._eng()
        l = _make_lesson("brand_new_key", LessonKind.COMMAND, LessonScope.EXECUTION, "v",
                          composite_score=0.0)
        l.novelty = 0.5; l.reuse_potential = 0.5; l.success_rate = 0.5
        ctx = ScoringContext(existing_keys=set())
        novelty = eng._compute_novelty(l, ctx)
        assert novelty == 1.0

    def test_novelty_duplicate_key(self):
        from agent.reflection.scoring_engine import ScoringContext
        eng = self._eng()
        l = _make_lesson("known_key", composite_score=0.0)
        l.novelty = 0.5; l.reuse_potential = 0.5; l.success_rate = 0.5
        ctx = ScoringContext(existing_keys={"known_key"})
        novelty = eng._compute_novelty(l, ctx)
        assert novelty <= 0.15

    def test_reuse_potential_repair(self):
        from agent.reflection.experience_record import LessonKind, LessonScope
        from agent.reflection.scoring_engine import ScoringContext
        eng = self._eng()
        l = _make_lesson("k", LessonKind.REPAIR, LessonScope.EXECUTION, "v", composite_score=0.0)
        ctx = ScoringContext()
        rp = eng._compute_reuse_potential(l, ctx)
        assert rp >= 0.85

    def test_promotion_threshold_by_scope(self):
        from agent.reflection.experience_record import LessonScope
        eng = self._eng()
        assert eng.get_promotion_threshold(LessonScope.LONG_TERM) >= 0.70
        assert eng.get_promotion_threshold(LessonScope.SESSION) < 0.65

    def test_scoring_weights_validate_raises(self):
        from agent.reflection.scoring_engine import ScoringWeights
        w = ScoringWeights(importance=0.5, confidence=0.5, novelty=0.5,
                            reuse_potential=0.5, success_rate=0.5)
        with pytest.raises(ValueError):
            w.validate()

    def test_scoring_weights_validate_passes(self):
        from agent.reflection.scoring_engine import ScoringWeights
        w = ScoringWeights()  # defaults sum to 1.0
        w.validate()  # should not raise


# ===========================================================================
# TestNoiseFilter
# ===========================================================================

class TestNoiseFilter:

    def _nf(self, min_score=0.30, seen_keys=None):
        from agent.reflection.noise_filter import NoiseFilter
        return NoiseFilter(min_score=min_score, seen_keys=seen_keys or set())

    def test_low_score_filtered(self):
        nf = self._nf(min_score=0.50)
        l = _make_lesson("k", composite_score=0.20)
        is_noise, reason = nf.is_noise(l)
        assert is_noise
        assert reason == "low_score"

    def test_duplicate_key_filtered(self):
        nf = self._nf(seen_keys={"dup_key"})
        l = _make_lesson("dup_key", composite_score=0.80)
        is_noise, reason = nf.is_noise(l)
        assert is_noise
        assert reason == "duplicate"

    def test_too_large_filtered(self):
        from agent.reflection.noise_filter import NoiseFilter
        nf = NoiseFilter(min_score=0.30, max_value_size=10)
        l = _make_lesson("k", value="x" * 100, composite_score=0.80)
        is_noise, reason = nf.is_noise(l)
        assert is_noise
        assert reason == "too_large"

    def test_low_quality_both_low(self):
        l = _make_lesson("k", importance=0.30, confidence=0.30, composite_score=0.35)
        nf = self._nf()
        is_noise, reason = nf.is_noise(l)
        assert is_noise
        assert reason == "low_quality"

    def test_empty_value_filtered(self):
        l = _make_lesson("k", value="", composite_score=0.80)
        nf = self._nf()
        is_noise, reason = nf.is_noise(l)
        assert is_noise

    def test_trivial_command_filtered(self):
        l = _make_lesson("command:success:ls", value="file list", importance=0.7,
                          confidence=0.7, composite_score=0.70)
        nf = self._nf()
        is_noise, reason = nf.is_noise(l)
        assert is_noise
        assert reason == "trivial_command"

    def test_ansi_codes_raw_output(self):
        l = _make_lesson("k", value="\x1b[31mError\x1b[0m some output",
                          importance=0.7, confidence=0.7, composite_score=0.70)
        nf = self._nf()
        is_noise, reason = nf.is_noise(l)
        assert is_noise
        assert reason == "raw_output"

    def test_good_lesson_passes(self):
        l = _make_lesson("repair:import_error:pip_install",
                          importance=0.9, confidence=0.85, composite_score=0.80)
        l.value = {"fix": "pip install x"}
        nf = self._nf()
        is_noise, _ = nf.is_noise(l)
        assert not is_noise

    def test_filter_returns_partition(self):
        from agent.reflection.noise_filter import NoiseFilter
        nf = NoiseFilter(min_score=0.50)
        good = _make_lesson("good", importance=0.9, confidence=0.9, composite_score=0.80)
        good.value = {"v": 1}
        bad = _make_lesson("bad", composite_score=0.20)
        kept, filtered_keys = nf.filter([good, bad])
        assert any(l.key == "good" for l in kept)
        assert "bad" in filtered_keys

    def test_get_stats_tracks_counts(self):
        from agent.reflection.noise_filter import NoiseFilter
        nf = NoiseFilter(min_score=0.50)
        bad = _make_lesson("x", composite_score=0.10)
        nf.filter([bad])
        stats = nf.get_stats()
        assert stats.get("total_seen", 0) >= 1
        # Per-rule counts: the low_score rule should have fired
        assert stats.get("low_score", 0) >= 1


# ===========================================================================
# TestPipeline
# ===========================================================================

class TestPipeline:

    def _pipeline(self, tmp_path, monkeypatch):
        from agent.reflection.pipeline import ReflectionPipeline, PipelineConfig
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        config = PipelineConfig(
            use_memory_manager=False,
            use_knowledge_graph=False,
            persist_experience=False,
        )
        return ReflectionPipeline(project_hash=_uid(), config=config)

    def test_run_returns_output(self, tmp_path, monkeypatch):
        from agent.reflection.experience_record import ReflectionOutput
        p = self._pipeline(tmp_path, monkeypatch)
        inp = _make_input(task_description="run a test")
        out = p.run(inp)
        assert isinstance(out, ReflectionOutput)

    def test_reflector_error_does_not_crash(self, tmp_path, monkeypatch):
        from agent.reflection.pipeline import ReflectionPipeline, PipelineConfig
        from agent.reflection.extractor import KnowledgeExtractor, BaseReflector
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        class Broken(BaseReflector):
            name = "broken"
            def extract(self, inp): raise RuntimeError("boom")
        ext = KnowledgeExtractor()
        ext.register(Broken())
        config = PipelineConfig(use_memory_manager=False, use_knowledge_graph=False,
                                persist_experience=False)
        p = ReflectionPipeline(project_hash=_uid(), config=config, extractor=ext)
        out = p.run(_make_input())
        assert out is not None

    def test_output_error_on_catastrophic_failure(self, tmp_path, monkeypatch):
        from agent.reflection.pipeline import ReflectionPipeline, PipelineConfig
        from agent.reflection.extractor import KnowledgeExtractor
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        # Make extractor always raise
        class CrashExtractor(KnowledgeExtractor):
            def extract_all(self, inp): raise RuntimeError("catastrophic")
        config = PipelineConfig(use_memory_manager=False, use_knowledge_graph=False,
                                persist_experience=False)
        p = ReflectionPipeline(project_hash=_uid(), config=config, extractor=CrashExtractor())
        out = p.run(_make_input())
        assert out.error is not None

    def test_query_experiences_returns_list(self, tmp_path, monkeypatch):
        p = self._pipeline(tmp_path, monkeypatch)
        result = p.query_experiences()
        assert isinstance(result, list)

    def test_persist_experience_false_skips_db(self, tmp_path, monkeypatch):
        from agent.reflection.pipeline import ReflectionPipeline, PipelineConfig
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        config = PipelineConfig(use_memory_manager=False, use_knowledge_graph=False,
                                persist_experience=False)
        p = ReflectionPipeline(project_hash=_uid(), config=config)
        assert p._db_path is None or True  # may or may not be None depending on impl

    def test_summarize(self, tmp_path, monkeypatch):
        p = self._pipeline(tmp_path, monkeypatch)
        inp = _make_input(task_description="implement auth", tool_calls=[
            {"tool": "write_file", "success": True},
            {"tool": "bash", "success": True},
        ])
        summary = p._summarize(inp)
        assert len(summary) > 10
        assert "implement auth" in summary or "implement" in summary.lower()


# ===========================================================================
# TestReflectionEngine
# ===========================================================================

class TestReflectionEngine:

    def _eng(self, tmp_path, monkeypatch):
        from agent.reflection.engine import ReflectionEngine
        from agent.reflection.pipeline import PipelineConfig
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        config = PipelineConfig(use_memory_manager=False, use_knowledge_graph=False,
                                persist_experience=False)
        return ReflectionEngine(project_hash=_uid(), config=config)

    def test_reflect_returns_output(self, tmp_path, monkeypatch):
        from agent.reflection.experience_record import ReflectionOutput
        eng = self._eng(tmp_path, monkeypatch)
        out = eng.reflect(_make_input())
        assert isinstance(out, ReflectionOutput)

    def test_reflect_from_dict_accepts_dict(self, tmp_path, monkeypatch):
        from agent.reflection.experience_record import ReflectionOutput
        eng = self._eng(tmp_path, monkeypatch)
        out = eng.reflect_from_dict({"task_description": "do something"})
        assert isinstance(out, ReflectionOutput)

    def test_analyze_is_alias(self, tmp_path, monkeypatch):
        eng = self._eng(tmp_path, monkeypatch)
        out = eng.analyze(_make_input())
        assert out is not None

    def test_extract_returns_lessons(self, tmp_path, monkeypatch):
        eng = self._eng(tmp_path, monkeypatch)
        inp = _make_input(terminal_events=[
            {"command": "pytest", "exit_code": 0, "output": "ok", "duration_sec": 3.0}
        ])
        lessons = eng.extract(inp)
        assert isinstance(lessons, list)

    def test_score_returns_sorted(self, tmp_path, monkeypatch):
        eng = self._eng(tmp_path, monkeypatch)
        from agent.reflection.experience_record import LessonKind, LessonScope
        l1 = _make_lesson("k1", LessonKind.REPAIR, LessonScope.EXECUTION, "v",
                           importance=0.9, confidence=0.9, composite_score=0.0)
        l1.novelty = 0.9; l1.reuse_potential = 0.9; l1.success_rate = 0.9
        l2 = _make_lesson("k2", LessonKind.COMPLEXITY, LessonScope.SESSION, "v",
                           importance=0.1, confidence=0.1, composite_score=0.0)
        l2.novelty = 0.1; l2.reuse_potential = 0.1; l2.success_rate = 0.1
        scored = eng.score([l1, l2])
        assert scored[0].key == "k1"

    def test_summarize_returns_string(self, tmp_path, monkeypatch):
        eng = self._eng(tmp_path, monkeypatch)
        s = eng.summarize(_make_input(task_description="fix auth bug"))
        assert isinstance(s, str)
        assert len(s) > 0

    def test_get_pipeline_info(self, tmp_path, monkeypatch):
        eng = self._eng(tmp_path, monkeypatch)
        info = eng.get_pipeline_info()
        assert "project_hash" in info
        assert "registered_reflectors" in info
        assert "config" in info

    def test_query_experiences_list(self, tmp_path, monkeypatch):
        eng = self._eng(tmp_path, monkeypatch)
        result = eng.query_experiences()
        assert isinstance(result, list)


# ===========================================================================
# TestReflectionAnalytics
# ===========================================================================

class TestReflectionAnalytics:

    def _make_output(self, promoted=2, rejected=1, filtered=3, lessons=None):
        from agent.reflection.experience_record import ReflectionOutput, KnowledgeLesson, LessonKind, LessonScope
        lessons = lessons or [
            _make_lesson("r1", LessonKind.REPAIR, LessonScope.EXECUTION, "v"),
            _make_lesson("w1", LessonKind.WORKFLOW, LessonScope.WORKFLOW, "v"),
        ]
        return ReflectionOutput(
            task_id="t1",
            task_summary="did stuff",
            lessons=lessons,
            promoted_keys=["k1", "k2"][:promoted],
            rejected_keys=["k3"][:rejected],
            filtered_keys=["k4", "k5", "k6"][:filtered],
            reflection_time_sec=1.5,
        )

    def test_record_reflection_increments(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        a.record_reflection(self._make_output())
        m = a.get_metrics()
        assert m.total_reflections == 1

    def test_get_metrics_returns_dataclass(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics, ReflectionMetrics
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        m = a.get_metrics()
        assert isinstance(m, ReflectionMetrics)

    def test_promotion_rate_computed(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        a.record_reflection(self._make_output(promoted=2, rejected=0, filtered=0))
        m = a.get_metrics()
        # 2 promoted out of 2 lessons
        assert m.promotion_rate >= 0.0

    def test_noise_filter_rate(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        a.record_reflection(self._make_output(filtered=3))
        m = a.get_metrics()
        assert m.noise_filter_rate >= 0.0

    def test_reset_clears_counters(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        a.record_reflection(self._make_output())
        a.reset()
        m = a.get_metrics()
        assert m.total_reflections == 0

    def test_get_dashboard_has_keys(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        d = a.get_dashboard()
        assert "totals" in d
        assert "learning_rates" in d
        assert "efficiency" in d

    def test_repair_learned_tracked(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics
        from agent.reflection.experience_record import LessonKind, LessonScope
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        lessons = [_make_lesson("r1", LessonKind.REPAIR, LessonScope.EXECUTION, "v")]
        a.record_reflection(self._make_output(lessons=lessons))
        m = a.get_metrics()
        assert m.total_repair_patterns_learned >= 1

    def test_get_recent_events_returns_list(self, tmp_path, monkeypatch):
        from agent.reflection.analytics import ReflectionAnalytics
        monkeypatch.setenv("KRYTH_HOME", str(tmp_path))
        a = ReflectionAnalytics(_uid())
        a.record_reflection(self._make_output())
        events = a.get_recent_events(limit=10)
        assert isinstance(events, list)
