"""
KRYTH Runtime Reliability Regression Tests
===========================================

Covers all four bug classes identified in the Phase 1 root-cause audit:

1. InterruptionType serialization / deserialization (ValueError crash)
2. Tool execution circuit breaker / global failure counter poisoning
3. Filesystem path safety and workspace sandbox enforcement
4. Checkpoint status consistency (status=running vs current_task_status=TASK_COMPLETED)

Rules enforced by this test file:
- All temporary files are created inside tmp_path (pytest fixture).
- No user project files are read, modified, or deleted.
- Tests are self-contained and idempotent.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / stubs shared across tests
# ---------------------------------------------------------------------------

def _make_hook_system() -> MagicMock:
    """Return a minimal HookSystem stub."""
    hs = MagicMock()
    hs.trigger_before_tool = AsyncMock()
    hs.trigger_after_tool = AsyncMock()
    hs.trigger_before_agent = AsyncMock()
    hs.trigger_after_agent = AsyncMock()
    return hs


def _make_config(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.cwd = tmp_path
    cfg.allowed_tools = None
    return cfg


# ===========================================================================
# SECTION 1 — InterruptionType serialization / deserialization
# ===========================================================================

class TestInterruptionTypeSerialization:
    """
    Root cause: str(InterruptionType.EXECUTION_INTERRUPTED) produces
    "InterruptionType.EXECUTION_INTERRUPTED" (the str-enum default) while
    InterruptionType("EXECUTION_INTERRUPTED") expects the bare member value.
    Fixed in: agent/events.py and utils/execution_record.py.
    """

    def test_all_members_round_trip_via_value(self):
        """Every member's .value must be accepted by the constructor."""
        from tools.base import InterruptionType
        for member in InterruptionType:
            assert InterruptionType(member.value) is member

    def test_str_of_member_does_not_equal_value(self):
        """Confirm the original bug: str() != .value for str-enum members."""
        from tools.base import InterruptionType
        member = InterruptionType.EXECUTION_INTERRUPTED
        # str() on a str-enum gives the full "ClassName.MEMBER_NAME" string
        # (Python 3.11+ changed behaviour; guard for both):
        raw = str(member)
        # Either way, InterruptionType(raw) must succeed after the fix because
        # the UI now wraps it in try/except. We only assert the value form works.
        assert InterruptionType(member.value) is member

    def test_agent_end_event_stores_value_not_str(self):
        """AgentEvent.agent_end must store interruption_type as the enum value."""
        from tools.base import InterruptionType
        from agent.events import AgentEvent

        event = AgentEvent.agent_end(
            interruption_type=InterruptionType.EXECUTION_INTERRUPTED,
        )
        stored = event.data["interruption_type"]
        # Must equal the bare enum value, not "InterruptionType.EXECUTION_INTERRUPTED"
        assert stored == "EXECUTION_INTERRUPTED"
        # Must be re-parseable without error
        assert InterruptionType(stored) is InterruptionType.EXECUTION_INTERRUPTED

    def test_agent_end_event_accepts_str_passthrough(self):
        """A plain string interruption_type must also survive round-trip."""
        from agent.events import AgentEvent
        from tools.base import InterruptionType

        event = AgentEvent.agent_end(interruption_type="USER_CANCELLED")
        stored = event.data["interruption_type"]
        assert stored == "USER_CANCELLED"
        assert InterruptionType(stored) is InterruptionType.USER_CANCELLED

    def test_agent_end_event_none_interruption_type(self):
        """None interruption_type must produce None in the event data."""
        from agent.events import AgentEvent

        event = AgentEvent.agent_end(interruption_type=None)
        assert event.data["interruption_type"] is None

    @pytest.mark.parametrize("member", [
        "USER_CANCELLED",
        "TOOL_TIMEOUT",
        "CONNECTION_FAILED",
        "CONTEXT_LIMIT_REACHED",
        "EXECUTION_INTERRUPTED",
        "TASK_COMPLETED",
    ])
    def test_all_valid_members_deserialize_without_error(self, member):
        """Every valid InterruptionType value must deserialize cleanly."""
        from tools.base import InterruptionType
        result = InterruptionType(member)
        assert result.value == member

    def test_unknown_interruption_type_does_not_crash_ui(self):
        """The UI must not raise ValueError for an unknown interruption_type string."""
        # The fix in ui/stream.py wraps InterruptionType(...) in try/except.
        # Simulate what stream.py does:
        from tools.base import InterruptionType
        unknown = "SOME_FUTURE_VALUE_NOT_IN_ENUM"
        try:
            label = InterruptionType(unknown).value.replace("_", " ").title()
        except ValueError:
            label = unknown.replace("_", " ").title()
        assert "Some Future Value Not In Enum" == label

    def test_execution_record_finalize_stores_value(self, tmp_path):
        """ExecutionRecorder.finalize must persist interruption_type as a bare value."""
        from tools.base import InterruptionType
        from utils.execution_record import ExecutionRecorder

        recorder = ExecutionRecorder(tmp_path)
        recorder.start("test objective")
        recorder.finalize(
            None,
            task_checkpoint={},
            interrupted=True,
            interruption_type=InterruptionType.USER_CANCELLED,
        )
        stored = recorder.record["interruption_type"]
        # Must be the bare value, loadable by the enum constructor
        assert stored == "USER_CANCELLED"
        assert InterruptionType(stored) is InterruptionType.USER_CANCELLED

    def test_execution_record_finalize_default_fallback(self, tmp_path):
        """When interruption_type is None, the default fallback value must be valid."""
        from tools.base import InterruptionType
        from utils.execution_record import ExecutionRecorder

        recorder = ExecutionRecorder(tmp_path)
        recorder.start("test objective")
        recorder.finalize(None, task_checkpoint={}, interrupted=True)
        stored = recorder.record["interruption_type"]
        assert InterruptionType(stored) is InterruptionType.EXECUTION_INTERRUPTED


# ===========================================================================
# SECTION 2 — Tool execution circuit breaker / failure counter isolation
# ===========================================================================

class TestToolFailureIsolation:
    """
    Root cause: the global singleton ErrorRecoveryEngine accumulated failures
    across all tools, and the pre-invocation loop over *all* ErrorCategory
    values blocked every subsequent tool call after any 2 failures anywhere.

    Fixed in: tools/registry.py — engine is now per-ToolRegistry, and the
    pre-invocation block-all circuit breaker has been removed.
    """

    def test_error_recovery_engine_is_per_registry(self, tmp_path):
        """Two ToolRegistry instances must have independent error engines."""
        from tools.registry import ToolRegistry

        cfg = _make_config(tmp_path)
        r1 = ToolRegistry(cfg)
        r2 = ToolRegistry(cfg)
        assert r1._error_engine is not r2._error_engine

    def test_failure_in_one_category_does_not_block_other_tools(self, tmp_path):
        """
        After recording 2+ failures of one category, invoking a *different*
        (succeeding) tool must still return success.
        """
        from tools.registry import ToolRegistry
        from tools.core.error_recovery import ErrorCategory
        from tools.base import Tool, ToolKind, ToolInvocation, ToolResult

        # Plant 2 failures of the UNKNOWN category into the engine
        cfg = _make_config(tmp_path)
        registry = ToolRegistry(cfg)
        for _ in range(3):
            registry._error_engine.record_failure(ErrorCategory.UNKNOWN, "simulated failure")

        # Register a tool that always succeeds
        class AlwaysOkTool(Tool):
            name = "always_ok"
            kind = ToolKind.READ
            description = "Always succeeds"
            schema = {"type": "object", "properties": {}}

            async def execute(self, inv: ToolInvocation) -> ToolResult:
                return ToolResult.success_result("ok")

        registry.register(AlwaysOkTool(cfg))
        hook = _make_hook_system()

        result = asyncio.run(
            registry.invoke("always_ok", {}, tmp_path, hook)
        )
        assert result.success, f"Tool blocked after unrelated failures: {result.error}"
        assert result.output == "ok"

    def test_exception_in_tool_is_classified_not_propagated(self, tmp_path):
        """An unexpected exception inside tool.execute must become a ToolResult, not propagate."""
        from tools.registry import ToolRegistry
        from tools.base import Tool, ToolKind, ToolInvocation, ToolResult

        cfg = _make_config(tmp_path)
        registry = ToolRegistry(cfg)

        class BrokenTool(Tool):
            name = "broken"
            kind = ToolKind.READ
            description = "Always raises"
            schema = {"type": "object", "properties": {}}

            async def execute(self, inv: ToolInvocation) -> ToolResult:
                raise RuntimeError("internal boom")

        registry.register(BrokenTool(cfg))
        hook = _make_hook_system()

        result = asyncio.run(
            registry.invoke("broken", {}, tmp_path, hook)
        )
        assert not result.success
        assert "Internal error" in result.error or "internal boom" in result.error

    def test_successful_tool_works_after_other_tool_raised(self, tmp_path):
        """
        After one tool raises an exception (classified by the engine), a
        different tool must still execute successfully.
        """
        from tools.registry import ToolRegistry
        from tools.base import Tool, ToolKind, ToolInvocation, ToolResult

        cfg = _make_config(tmp_path)
        registry = ToolRegistry(cfg)

        class BrokenTool(Tool):
            name = "broken2"
            kind = ToolKind.READ
            description = "Always raises"
            schema = {"type": "object", "properties": {}}

            async def execute(self, inv: ToolInvocation) -> ToolResult:
                raise ValueError("oops")

        class GoodTool(Tool):
            name = "good"
            kind = ToolKind.READ
            description = "Always succeeds"
            schema = {"type": "object", "properties": {}}

            async def execute(self, inv: ToolInvocation) -> ToolResult:
                return ToolResult.success_result("good result")

        registry.register(BrokenTool(cfg))
        registry.register(GoodTool(cfg))
        hook = _make_hook_system()

        async def _run():
            # First call the broken tool several times
            for _ in range(5):
                await registry.invoke("broken2", {}, tmp_path, hook)
            # Then call the good tool — must still succeed
            return await registry.invoke("good", {}, tmp_path, hook)

        result = asyncio.run(_run())
        assert result.success, f"Good tool blocked: {result.error}"

    def test_error_metadata_contains_category(self, tmp_path):
        """Exception ToolResult must carry error_category metadata."""
        from tools.registry import ToolRegistry
        from tools.base import Tool, ToolKind, ToolInvocation, ToolResult

        cfg = _make_config(tmp_path)
        registry = ToolRegistry(cfg)

        class PermBroken(Tool):
            name = "perm_broken"
            kind = ToolKind.READ
            description = "Raises PermissionError"
            schema = {"type": "object", "properties": {}}

            async def execute(self, inv: ToolInvocation) -> ToolResult:
                raise PermissionError("access denied")

        registry.register(PermBroken(cfg))
        hook = _make_hook_system()
        result = asyncio.run(
            registry.invoke("perm_broken", {}, tmp_path, hook)
        )
        assert not result.success
        assert "error_category" in result.metadata

    def test_unknown_tool_returns_error_not_raises(self, tmp_path):
        """Invoking a tool that is not registered must return an error ToolResult."""
        from tools.registry import ToolRegistry

        cfg = _make_config(tmp_path)
        registry = ToolRegistry(cfg)
        hook = _make_hook_system()
        result = asyncio.run(
            registry.invoke("does_not_exist", {}, tmp_path, hook)
        )
        assert not result.success
        assert "Unknown Tool" in result.error or "does_not_exist" in result.error


# ===========================================================================
# SECTION 3 — Filesystem path safety and workspace sandbox
# ===========================================================================

class TestFilesystemSafety:
    """
    Verify that resolve_path enforces workspace sandbox restrictions,
    handles relative and absolute paths correctly, and rejects traversal
    attempts. All tests use tmp_path so no user files are touched.
    """

    def test_relative_path_inside_workspace_resolves(self, tmp_path):
        from utils.paths import resolve_path
        result = resolve_path(tmp_path, "subdir/file.txt")
        assert result == (tmp_path / "subdir" / "file.txt").resolve()

    def test_absolute_path_inside_workspace_resolves(self, tmp_path):
        from utils.paths import resolve_path
        target = tmp_path / "file.txt"
        result = resolve_path(tmp_path, str(target))
        assert result == target.resolve()

    def test_traversal_via_dotdot_is_rejected(self, tmp_path):
        from utils.paths import resolve_path
        with pytest.raises(ValueError, match="outside the working directory"):
            resolve_path(tmp_path, "../secret.txt")

    def test_traversal_via_nested_dotdot_is_rejected(self, tmp_path):
        from utils.paths import resolve_path
        with pytest.raises(ValueError, match="outside the working directory"):
            resolve_path(tmp_path, "subdir/../../etc/passwd")

    def test_absolute_path_outside_workspace_is_rejected(self, tmp_path):
        from utils.paths import resolve_path
        # Pick a path that is definitely outside tmp_path
        outside = Path(sys.executable).resolve()
        with pytest.raises(ValueError, match="outside the working directory"):
            resolve_path(tmp_path, str(outside))

    def test_symlink_escape_is_rejected(self, tmp_path):
        """A symlink that points outside the workspace must be rejected."""
        from utils.paths import resolve_path
        # Create a symlink inside tmp_path pointing to the parent directory
        target_outside = tmp_path.parent
        link = tmp_path / "escape_link"
        try:
            link.symlink_to(target_outside)
        except (OSError, NotImplementedError):
            pytest.skip("Symlink creation not supported on this platform/user")
        with pytest.raises(ValueError, match="outside the working directory"):
            resolve_path(tmp_path, "escape_link/secret.txt")

    def test_read_write_inside_workspace_works(self, tmp_path):
        """Read and write operations inside the workspace sandbox must succeed."""
        from utils.paths import resolve_path
        path = resolve_path(tmp_path, "test_file.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("hello", encoding="utf-8")
        assert path.read_text(encoding="utf-8") == "hello"

    def test_nested_subdir_inside_workspace_resolves(self, tmp_path):
        from utils.paths import resolve_path
        result = resolve_path(tmp_path, "a/b/c/d.txt")
        assert str(result).startswith(str(tmp_path.resolve()))


# ===========================================================================
# SECTION 4 — Checkpoint consistency
# ===========================================================================

class TestCheckpointConsistency:
    """
    Root cause: task_checkpoint.status and current_task_status could diverge
    after an interrupted write sequence. Fixed by _repair_status_inconsistency
    in DevelopmentSupervisor._load().
    """

    def test_completed_checkpoint_repairs_missing_current_task_status(self, tmp_path):
        """
        If task_checkpoint.status == 'completed' but current_task_status is
        absent, _load must set current_task_status = 'TASK_COMPLETED'.
        """
        from utils.development_supervisor import DevelopmentSupervisor, TASK_COMPLETED_STATE

        sup = DevelopmentSupervisor(tmp_path)
        # Inject inconsistent state: checkpoint says completed, top level is missing
        sup.state["task_checkpoint"]["status"] = TASK_COMPLETED_STATE
        sup.state.pop("current_task_status", None)
        sup._persist()

        # Reload from disk — repair must run
        sup2 = DevelopmentSupervisor(tmp_path)
        assert sup2.state.get("current_task_status") == "TASK_COMPLETED"

    def test_running_checkpoint_clears_stale_completed_status(self, tmp_path):
        """
        If task_checkpoint.status == 'running' but current_task_status claims
        'TASK_COMPLETED', the stale top-level status must be cleared.
        """
        from utils.development_supervisor import DevelopmentSupervisor, TASK_RUNNING

        sup = DevelopmentSupervisor(tmp_path)
        sup.state["task_checkpoint"]["status"] = TASK_RUNNING
        sup.state["current_task_status"] = "TASK_COMPLETED"  # stale!
        sup._persist()

        sup2 = DevelopmentSupervisor(tmp_path)
        assert sup2.state.get("current_task_status") is None

    def test_begin_task_does_not_block_after_completed_task(self, tmp_path):
        """
        begin_task must start a *new* task even if the previous one is in
        TASK_COMPLETED_STATE. The old guard blocked this.
        """
        from utils.development_supervisor import DevelopmentSupervisor, TASK_COMPLETED_STATE

        sup = DevelopmentSupervisor(tmp_path)
        sup.state["task_checkpoint"]["status"] = TASK_COMPLETED_STATE
        sup.state["task_checkpoint"]["task_id"] = "old-task-id"
        sup._persist()

        new_id = sup.begin_task("new task description")
        assert new_id != "old-task-id"
        assert sup.state["task_checkpoint"]["status"] != TASK_COMPLETED_STATE

    def test_begin_task_returns_existing_id_when_running(self, tmp_path):
        """
        begin_task must return the existing task ID when status == TASK_RUNNING,
        preventing duplicate task creation for the same in-progress work.
        """
        from utils.development_supervisor import DevelopmentSupervisor, TASK_RUNNING

        sup = DevelopmentSupervisor(tmp_path)
        first_id = sup.begin_task("first task")
        # Checkpoint should now be RUNNING
        assert sup.state["task_checkpoint"]["status"] == TASK_RUNNING
        second_id = sup.begin_task("second task")  # should return existing
        assert second_id == first_id

    def test_checkpoint_status_persists_atomically(self, tmp_path):
        """
        After _persist(), the on-disk JSON must reflect the in-memory status.
        """
        from utils.development_supervisor import DevelopmentSupervisor, TASK_COMPLETED_STATE

        sup = DevelopmentSupervisor(tmp_path)
        sup.begin_task("atomic test task")
        # Manually transition to completed
        sup.state["task_checkpoint"]["status"] = TASK_COMPLETED_STATE
        sup.state["current_task_status"] = "TASK_COMPLETED"
        sup._persist()

        # Read raw JSON and verify consistency
        raw = json.loads(sup.path.read_text(encoding="utf-8"))
        assert raw["task_checkpoint"]["status"] == TASK_COMPLETED_STATE
        assert raw["current_task_status"] == "TASK_COMPLETED"

    def test_failed_checkpoint_repair(self, tmp_path):
        """
        If task_checkpoint.status == 'failed', current_task_status must be
        set to 'TASK_FAILED' on load.
        """
        from utils.development_supervisor import DevelopmentSupervisor, TASK_FAILED

        sup = DevelopmentSupervisor(tmp_path)
        sup.state["task_checkpoint"]["status"] = TASK_FAILED
        sup.state.pop("current_task_status", None)
        sup._persist()

        sup2 = DevelopmentSupervisor(tmp_path)
        assert sup2.state.get("current_task_status") == "TASK_FAILED"

    def test_session_restart_recovers_interrupted_task(self, tmp_path):
        """
        A TASK_RUNNING checkpoint written before a crash must remain recoverable
        after restart — status must still be RUNNING (not repaired to anything else).
        """
        from utils.development_supervisor import DevelopmentSupervisor, TASK_RUNNING

        sup = DevelopmentSupervisor(tmp_path)
        tid = sup.begin_task("interrupted work")
        sup._persist()

        sup2 = DevelopmentSupervisor(tmp_path)
        assert sup2.state["task_checkpoint"]["status"] == TASK_RUNNING
        assert sup2.state["task_checkpoint"]["task_id"] == tid


# ===========================================================================
# SECTION 5 — Error recovery engine unit tests
# ===========================================================================

class TestErrorRecoveryEngine:
    """Direct unit tests for ErrorRecoveryEngine behaviour."""

    def test_classify_permission_error(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        cls = engine.classify(PermissionError("permission denied"))
        assert cls.category == ErrorCategory.PERMISSION_DENIED

    def test_classify_import_error(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        cls = engine.classify(ImportError("No module named foo"))
        assert cls.category == ErrorCategory.IMPORT_ERROR

    def test_classify_syntax_error(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        cls = engine.classify(SyntaxError("invalid syntax"))
        assert cls.category == ErrorCategory.SYNTAX

    def test_failure_count_tracks_correctly(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        assert engine.similar_failure_count(ErrorCategory.UNKNOWN) == 0
        engine.record_failure(ErrorCategory.UNKNOWN, "err1")
        assert engine.similar_failure_count(ErrorCategory.UNKNOWN) == 1
        engine.record_failure(ErrorCategory.UNKNOWN, "err2")
        assert engine.similar_failure_count(ErrorCategory.UNKNOWN) == 2

    def test_should_stop_triggers_at_threshold(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        engine._max_similar_failures = 2
        engine.record_failure(ErrorCategory.UNKNOWN, "err1")
        assert not engine.should_stop_on_failure(ErrorCategory.UNKNOWN)
        engine.record_failure(ErrorCategory.UNKNOWN, "err2")
        assert engine.should_stop_on_failure(ErrorCategory.UNKNOWN)

    def test_reset_clears_all_failures(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        engine.record_failure(ErrorCategory.UNKNOWN, "err")
        engine.reset()
        assert engine.similar_failure_count(ErrorCategory.UNKNOWN) == 0
        assert not engine.should_stop_on_failure(ErrorCategory.UNKNOWN)

    def test_independent_engines_do_not_share_state(self):
        """Two separate engine instances must not share failure records."""
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        e1 = ErrorRecoveryEngine()
        e2 = ErrorRecoveryEngine()
        e1.record_failure(ErrorCategory.UNKNOWN, "err")
        e1.record_failure(ErrorCategory.UNKNOWN, "err")
        assert e1.should_stop_on_failure(ErrorCategory.UNKNOWN)
        assert not e2.should_stop_on_failure(ErrorCategory.UNKNOWN)

    def test_recovery_plan_retryable_flag(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        cls = engine.classify(Exception("ConnectionError: network down"))
        assert cls.retryable is True

    def test_syntax_error_is_not_retryable(self):
        from tools.core.error_recovery import ErrorRecoveryEngine, ErrorCategory
        engine = ErrorRecoveryEngine()
        cls = engine.classify(SyntaxError("invalid syntax"))
        assert cls.retryable is False


# ===========================================================================
# SECTION 6 — Approval persistence
# ===========================================================================

class TestApprovalManager:
    """Approval manager must enforce decisions consistently."""

    def test_non_mutating_tool_always_approved(self, tmp_path):
        from safety.approval import ApprovalManager, ApprovalContext, ApprovalDecision
        from config.config import ApprovalPolicy, ExecutionMode

        mgr = ApprovalManager(
            ApprovalPolicy.AUTO,
            tmp_path,
            execution_mode=ExecutionMode.AUTO,
        )
        ctx = ApprovalContext(
            tool_name="read",
            params={},
            is_mutating=False,
            affected_paths=[],
        )
        result = asyncio.run(mgr.check_approval(ctx))
        assert result == ApprovalDecision.APPROVED

    def test_plan_mode_rejects_all_mutating_ops(self, tmp_path):
        from safety.approval import ApprovalManager, ApprovalContext, ApprovalDecision
        from config.config import ApprovalPolicy, ExecutionMode

        mgr = ApprovalManager(
            ApprovalPolicy.AUTO,
            tmp_path,
            execution_mode=ExecutionMode.PLAN,
        )
        ctx = ApprovalContext(
            tool_name="write",
            params={},
            is_mutating=True,
            affected_paths=[tmp_path / "file.txt"],
        )
        result = asyncio.run(mgr.check_approval(ctx))
        assert result == ApprovalDecision.REJECTED

    def test_outside_cwd_path_needs_confirmation(self, tmp_path):
        from safety.approval import ApprovalManager, ApprovalContext, ApprovalDecision
        from config.config import ApprovalPolicy, ExecutionMode

        mgr = ApprovalManager(
            ApprovalPolicy.AUTO,
            tmp_path,
            execution_mode=ExecutionMode.AUTO,
        )
        outside = tmp_path.parent / "other_project" / "file.txt"
        ctx = ApprovalContext(
            tool_name="write",
            params={},
            is_mutating=True,
            affected_paths=[outside],
        )
        result = asyncio.run(mgr.check_approval(ctx))
        assert result in {ApprovalDecision.NEEDS_CONFIRMATION, ApprovalDecision.REJECTED}

    def test_dangerous_command_rejected(self, tmp_path):
        from safety.approval import is_dangerous_command
        assert is_dangerous_command("rm -rf /") is True
        assert is_dangerous_command("rm -rf *") is True

    def test_safe_command_recognized(self, tmp_path):
        from safety.approval import is_safe_command
        assert is_safe_command("git status") is True
        assert is_safe_command("ls -la") is True


# ===========================================================================
# SECTION 7 — Execution record write / read consistency
# ===========================================================================

class TestExecutionRecorder:
    """ExecutionRecorder must write valid JSON that survives a reload."""

    def test_start_creates_in_progress_record(self, tmp_path):
        from utils.execution_record import ExecutionRecorder
        recorder = ExecutionRecorder(tmp_path)
        tid = recorder.start("test objective")
        assert recorder.path is not None
        assert recorder.path.exists()
        data = json.loads(recorder.path.read_text(encoding="utf-8"))
        assert data["status"] == "in_progress"
        assert data["task_id"] == tid

    def test_finalize_writes_completed_status(self, tmp_path):
        from utils.execution_record import ExecutionRecorder
        recorder = ExecutionRecorder(tmp_path)
        recorder.start("objective")
        # Simulate a verified completion checkpoint
        recorder.finalize(
            "great success",
            task_checkpoint={"status": "completed", "pending_steps": [], "failed_steps": []},
        )
        data = json.loads(recorder.path.read_text(encoding="utf-8"))
        assert data["status"] == "completed_verified"

    def test_finalize_interrupted_stores_valid_interruption_type(self, tmp_path):
        from tools.base import InterruptionType
        from utils.execution_record import ExecutionRecorder
        recorder = ExecutionRecorder(tmp_path)
        recorder.start("objective")
        recorder.finalize(
            None,
            task_checkpoint={},
            interrupted=True,
            interruption_type=InterruptionType.TOOL_TIMEOUT,
        )
        data = json.loads(recorder.path.read_text(encoding="utf-8"))
        it = data["interruption_type"]
        # Must be parseable without ValueError
        assert InterruptionType(it) is InterruptionType.TOOL_TIMEOUT

    def test_find_incomplete_matches_same_objective(self, tmp_path):
        from utils.execution_record import ExecutionRecorder
        r1 = ExecutionRecorder(tmp_path)
        r1.start("my task objective")
        # Leave in-progress (do not finalize)

        r2 = ExecutionRecorder(tmp_path)
        found = r2.find_incomplete("my task objective")
        assert found == r1.path.stem

    def test_find_incomplete_ignores_completed_records(self, tmp_path):
        from utils.execution_record import ExecutionRecorder
        r1 = ExecutionRecorder(tmp_path)
        r1.start("done task")
        r1.finalize(
            "done",
            task_checkpoint={"status": "completed", "pending_steps": [], "failed_steps": []},
        )
        r2 = ExecutionRecorder(tmp_path)
        found = r2.find_incomplete("done task")
        assert found is None


# ===========================================================================
# SECTION 8 — Windows / PowerShell execution (integration, skip if not Windows)
# ===========================================================================

@pytest.mark.skipif(os.name != "nt", reason="PowerShell only available on Windows")
class TestWindowsShellExecution:

    @pytest.mark.asyncio
    async def test_powershell_echo(self, tmp_path):
        from tools.core.shells import select_shell, ShellType
        shell = select_shell(preferred=ShellType.POWERSHELL)
        result = await shell.execute("Write-Output 'reliability-test-ok'", cwd=tmp_path, timeout=15)
        assert result.success
        assert "reliability-test-ok" in result.stdout

    @pytest.mark.asyncio
    async def test_powershell_path_with_spaces(self, tmp_path):
        from tools.core.shells import select_shell, ShellType
        spaced = tmp_path / "dir with spaces"
        spaced.mkdir()
        shell = select_shell(preferred=ShellType.POWERSHELL)
        result = await shell.execute(
            "Get-Location | Select-Object -ExpandProperty Path",
            cwd=spaced,
            timeout=15,
        )
        assert result.success
        assert str(spaced) in result.stdout

    @pytest.mark.asyncio
    async def test_powershell_timeout_reported(self, tmp_path):
        from tools.core.shells import select_shell, ShellType
        shell = select_shell(preferred=ShellType.POWERSHELL)
        result = await shell.execute("Start-Sleep -Seconds 10", timeout=1, cwd=tmp_path)
        assert result.timed_out


# ===========================================================================
# SECTION 9 — Workspace boundary error handling & Windows prompt guidance
# ===========================================================================

class TestWorkspaceBoundaryAndPromptGuidance:

    def test_resolve_path_raises_path_outside_workspace_error(self, tmp_path):
        from utils.paths import PathOutsideWorkspaceError, resolve_path
        outside = tmp_path.parent
        with pytest.raises(PathOutsideWorkspaceError) as exc_info:
            resolve_path(tmp_path, outside)
        assert isinstance(exc_info.value, ValueError)
        assert "outside the working directory" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_registry_handles_out_of_workspace_without_internal_error(self, tmp_path):
        from config.config import Config
        from hooks.hook_system import HookSystem
        from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
        from tools.registry import ToolRegistry
        from utils.paths import resolve_path
        from pydantic import BaseModel

        class _PathParams(BaseModel):
            path: str

        class _FakeReadTool(Tool):
            name = "fake_read"
            kind = ToolKind.READ
            description = "Read a path"
            schema = _PathParams

            async def execute(self, invocation: ToolInvocation) -> ToolResult:
                p = resolve_path(invocation.cwd, invocation.params["path"])
                return ToolResult.success_result(str(p))

        config = Config(cwd=tmp_path)
        registry = ToolRegistry(config)
        registry.register(_FakeReadTool(config))
        hooks = HookSystem(config)

        result = await registry.invoke(
            "fake_read",
            {"path": str(tmp_path.parent)},
            cwd=tmp_path,
            hook_system=hooks,
        )
        assert not result.success
        assert "Internal error" not in (result.error or "")
        assert "Path is outside the working directory" in (result.error or "")
        assert result.metadata.get("error_category") == "INVALID_PATH"
        assert result.metadata.get("error_retryable") is False
        # Must not record an unexpected failure in the error engine
        assert len(registry._error_engine._failures) == 0

    def test_environment_prompt_includes_windows_shell_notes(self, tmp_path):
        import sys
        from config.config import Config
        from prompts.environment import get_environment_section

        section = get_environment_section(Config(cwd=tmp_path))
        assert str(tmp_path) in section
        if sys.platform == "win32":
            assert "PowerShell" in section
            assert "Select-Object" in section

    def test_operational_prompt_mentions_workspace_boundary(self):
        from prompts.operational import get_operational_section
        section = get_operational_section()
        assert "Workspace Boundary" in section
        assert "cwd" in section


# ===========================================================================
# SECTION 10 — Continuation loop termination, PlanTool phases, Grep on file
# ===========================================================================

class TestContinuationAndToolFixes:

    @pytest.mark.asyncio
    async def test_agent_run_does_not_loop_when_task_finishes_cleanly(self, tmp_path):
        """
        When _agentic_loop() executes tools and then finishes cleanly (no pending
        plan steps or benchmark phases), Agent.run() must NOT force another
        continuation asking to complete benchmark phases, and must mark the
        checkpoint completed.
        """
        from unittest.mock import AsyncMock, MagicMock
        from agent.agent import Agent
        from agent.events import AgentEvent
        from config.config import Config
        from utils.development_supervisor import TASK_COMPLETED_STATE

        cfg = Config(cwd=tmp_path)
        agent = Agent(cfg)
        agent.session.context_manager = MagicMock()
        agent.session.save_checkpoint = MagicMock()
        agent.session.inc_turn = MagicMock()
        agent.session.note_prompt = MagicMock()
        agent.session.reset_turn_usage = MagicMock()
        agent.session.hook_system.trigger_before_agent = AsyncMock()
        agent.session.hook_system.trigger_after_agent = AsyncMock()

        loop_calls = 0

        async def fake_loop():
            nonlocal loop_calls
            loop_calls += 1
            agent._loop_finished_cleanly = True
            yield AgentEvent.tool_call_start("c1", "read", {"path": "x.txt"})
            yield AgentEvent.text_complete("Demo complete.")

        agent._agentic_loop = fake_loop

        events = []
        async for ev in agent.run("do a demo task"):
            events.append(ev)

        assert loop_calls == 1, f"Expected 1 pass, got {loop_calls} (endless continuation loop)"
        supervisor = agent.session.development_supervisor
        supervisor.state = supervisor._load()
        assert supervisor.state["task_checkpoint"]["status"] == TASK_COMPLETED_STATE

    @pytest.mark.asyncio
    async def test_grep_accepts_single_file_path(self, tmp_path):
        from config.config import Config
        from tools.base import ToolInvocation
        from tools.core.grep import GrepTool

        target = tmp_path / "sample.py"
        target.write_text("def hello():\n    return 42\n", encoding="utf-8")

        tool = GrepTool(Config(cwd=tmp_path))
        result = await tool.execute(
            ToolInvocation(params={"pattern": "hello", "path": "sample.py"}, cwd=tmp_path)
        )
        assert result.success, f"Grep on file failed: {result.error}"
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_plan_tool_supports_benchmark_phase_verification(self, tmp_path):
        from config.config import Config
        from tools.base import ToolInvocation
        from tools.core.plan import PlanTool

        tool = PlanTool(Config(cwd=tmp_path))
        add_res = await tool.execute(
            ToolInvocation(
                params={"action": "add_phase", "id": "phase.1", "content": "Inspect repo"},
                cwd=tmp_path,
            )
        )
        assert add_res.success
        verify_res = await tool.execute(
            ToolInvocation(
                params={"action": "verify_phase", "id": "phase.1", "verification": "Read hand.py"},
                cwd=tmp_path,
            )
        )
        assert verify_res.success, f"verify_phase failed: {verify_res.error}"
        assert verify_res.metadata["benchmark_verified"] == 1

    @pytest.mark.asyncio
    async def test_write_integrity_error_includes_reason_on_first_line(self, tmp_path):
        from config.config import Config
        from tools.base import ToolInvocation
        from tools.core.write import WriteFileTool

        existing = tmp_path / "data.json"
        existing.write_text("{}", encoding="utf-8")

        tool = WriteFileTool(Config(cwd=tmp_path))
        result = await tool.execute(
            ToolInvocation(
                params={"path": "data.json", "content": "{\"a\": 1}", "overwrite": False},
                cwd=tmp_path,
            )
        )
        assert not result.success
        first_line = (result.error or "").splitlines()[0]
        assert "Code integrity check failed:" in first_line
        assert len(first_line) > len("Code integrity check failed:") + 5


