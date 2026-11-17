import json
from pathlib import Path

from tools.base import ExecutionStatus, ToolResult
from utils.execution_record import ExecutionRecorder


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
