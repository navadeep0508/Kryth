import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.base import ExecutionStatus, ToolResult
from utils.execution_record import ExecutionRecorder


def _stored(recorder: ExecutionRecorder) -> dict:
    return json.loads(recorder.path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Windows execution-persistence fixes
# ---------------------------------------------------------------------------

def test_concurrent_writes_do_not_collide_or_corrupt(tmp_path: Path):
    """Multiple concurrent _write() calls to the same task_id must not corrupt JSON.

    Reusing a single `{task_id}.tmp` path caused collisions under concurrent
    writes and Windows rename failures; each attempt now uses a unique temp name.
    """
    recorder = ExecutionRecorder(tmp_path)
    recorder.start("concurrent persistence test", task_id="conc-1")

    def write_many():
        for _ in range(20):
            recorder._write()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(write_many) for _ in range(8)]
        for future in futures:
            future.result()

    # The final file must be valid JSON and contain the full record.
    stored = _stored(recorder)
    assert stored["task_id"] == "conc-1"
    assert stored["status"] == "in_progress"
    # No orphaned temp files left behind by failed/colliding attempts.
    temps = list((tmp_path / ".kryth" / "executions").glob("*.tmp"))
    assert temps == [], f"leftover temp files: {[str(t) for t in temps]}"


def test_write_retries_and_recovers_from_permission_error(tmp_path: Path):
    """Simulated Windows PermissionError on rename is retried; no exception propagates."""
    recorder = ExecutionRecorder(tmp_path)
    recorder.start("locking test", task_id="lock-1")

    call_count = {"n": 0}
    real_replace = os.replace

    def fake_replace(src, dst):
        call_count["n"] += 1
        if call_count["n"] < 3:
            # Simulate Windows: destination/temp held open (antivirus, other process).
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)

    with patch("utils.execution_record.os.replace", side_effect=fake_replace):
        # Must not raise even though the first two rename attempts fail.
        recorder.set_phase("EXECUTE")

    # Record retained in memory and eventually persisted to disk.
    stored = _stored(recorder)
    assert stored["task_id"] == "lock-1"
    assert stored["phase"] == "EXECUTE"
    assert call_count["n"] == 3  # two failures + one success


def test_write_does_not_crash_when_all_retries_exhausted(tmp_path: Path):
    """When every rename attempt fails, the caller is not crashed; record stays in memory."""
    recorder = ExecutionRecorder(tmp_path)
    recorder.start("exhausted retries", task_id="exh-1")

    def always_fail(src, dst):
        raise PermissionError(5, "Access is denied")

    with patch("utils.execution_record.os.replace", side_effect=always_fail):
        # Must not raise even though persistence always fails.
        recorder.tool_started("call-1", "bash", {"command": "echo hi"})
        recorder.tool_completed(
            "call-1",
            ToolResult.success_result(
                "done",
                execution_status=ExecutionStatus.ACTION_EXECUTED,
                action_executed=True,
            ),
            duration_ms=5,
        )

    # In-memory record is complete even though disk persistence failed.
    assert recorder.record is not None
    assert recorder.record["task_id"] == "exh-1"
    assert len(recorder.record["tool_calls"]) == 1
    assert recorder.record["tool_calls"][0]["success"] is True
    assert recorder.record["tool_calls"][0]["action_executed"] is True

    # No orphaned temp files left behind.
    temps = list((tmp_path / ".kryth" / "executions").glob("*.tmp"))
    assert temps == [], f"leftover temp files: {[str(t) for t in temps]}"


def test_subsequent_write_succeeds_after_prior_failures(tmp_path: Path):
    """After failures stop, the next write persists the in-memory record to disk."""
    recorder = ExecutionRecorder(tmp_path)
    recorder.start("recovery test", task_id="rec-1")

    attempts = {"n": 0}
    real_replace = os.replace

    def flaky_replace(src, dst):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)

    with patch("utils.execution_record.os.replace", side_effect=flaky_replace):
        recorder.set_phase("EXECUTE")  # fails twice, succeeds on third attempt

    stored = _stored(recorder)
    assert stored["task_id"] == "rec-1"
    assert stored["phase"] == "EXECUTE"


def test_interrupted_write_leaves_destination_valid(tmp_path: Path):
    """A failed write must not leave a corrupted destination file behind.

    The previous valid disk content is preserved; only the in-memory record
    reflects the update until a later write succeeds.
    """
    recorder = ExecutionRecorder(tmp_path)
    recorder.start("interruption test", task_id="int-1")
    before = _stored(recorder)
    assert before["phase"] == "UNDERSTAND"

    with patch("utils.execution_record.os.replace", side_effect=PermissionError(5, "Access is denied")):
        recorder.set_phase("OBSERVE")  # in-memory updated, disk write fails

    # Destination on disk still holds the previous valid JSON (untouched).
    after_disk = _stored(recorder)
    assert after_disk["phase"] == "UNDERSTAND"
    # In-memory record reflects the update and will be retried on next write.
    assert recorder.record["phase"] == "OBSERVE"
    assert after_disk["task_id"] == recorder.record["task_id"]


def test_start_does_not_crash_when_initial_write_fails(tmp_path: Path):
    """Recorder.start() must not raise even if the first disk write fails."""

    def fail_replace(src, dst):
        raise PermissionError(5, "Access is denied")

    with patch("utils.execution_record.os.replace", side_effect=fail_replace):
        recorder = ExecutionRecorder(tmp_path)
        task_id = recorder.start("initial write fails", task_id="init-1")

    assert task_id == "init-1"
    assert recorder.record is not None
    assert recorder.record["task_id"] == "init-1"
    assert recorder.record["status"] == "in_progress"

    # A later write (once failures stop) can still persist the in-memory record.
    real_root = tmp_path / ".kryth" / "executions"
    with patch("utils.execution_record.os.replace", side_effect=os.replace):
        recorder.set_phase("PLAN")
    assert _stored(recorder)["phase"] == "PLAN"


def test_execution_record_persists_calls_recovery_context_and_redacted_arguments(tmp_path: Path):
    recorder = ExecutionRecorder(tmp_path)
    task_id = recorder.start("Run tests using token=secret-value", task_id="task-1")
    recorder.set_phase("EXECUTE")
    recorder.tool_started("call-1", "bash", {"command": "pytest -q", "api_key": "secret-value"})
    recorder.tool_completed(
        "call-1",
        ToolResult.error_result(
            "timeout",
            metadata={"recovery_action": "inspect process before retry"},
        ),
        duration_ms=12,
        output_reference=".kryth/tool-outputs/call-1.txt",
    )
    recorder.record_context_budget({"estimated_input_tokens": 120, "input_budget": 500})

    stored = json.loads(recorder.path.read_text(encoding="utf-8"))
    assert task_id == "task-1"
    assert stored["objective"] == "Run tests using token=[redacted]"
    assert stored["tool_calls"][0]["arguments"]["api_key"] == "[redacted]"
    assert stored["tool_calls"][0]["execution_status"] == ExecutionStatus.ACTION_FAILED.value
    assert stored["tool_calls"][0]["output_reference"].endswith("call-1.txt")
    assert stored["recovery_decisions"][0]["retry_scheduled"] is False
    assert stored["context_budget_stats"]["requests"] == 1


def test_execution_record_does_not_claim_completion_without_verified_checkpoint(tmp_path: Path):
    recorder = ExecutionRecorder(tmp_path)
    recorder.start("Edit code and validate")
    recorder.finalize("The files were updated.", task_checkpoint=None)

    stored = json.loads(recorder.path.read_text(encoding="utf-8"))
    assert stored["status"] == "answered_unverified"
    assert stored["user_objective_completed"] is False


def test_execution_record_marks_interruption_as_resumable(tmp_path: Path):
    recorder = ExecutionRecorder(tmp_path)
    recorder.start("Inspect and update project")
    recorder.finalize(None, task_checkpoint={"status": "in_progress", "pending_steps": ["verify"]})

    stored = json.loads(recorder.path.read_text(encoding="utf-8"))
    assert stored["status"] == "incomplete"
    assert stored["recovery_information"]["pending_steps"] == ["verify"]


def test_interrupted_execution_can_resume_only_for_same_objective(tmp_path: Path):
    first = ExecutionRecorder(tmp_path)
    task_id = first.start("Inspect and update project", task_id="resume-task")
    first.finalize(None, task_checkpoint=None, interrupted=True)

    resumed = ExecutionRecorder(tmp_path)
    assert resumed.find_incomplete("Inspect and update project") == task_id
    assert resumed.find_incomplete("Different task") is None
    assert resumed.start("Inspect and update project", task_id=task_id) == task_id
    assert resumed.record["status"] == "in_progress"
    assert resumed.record["events"][-1]["type"] == "resumed"
