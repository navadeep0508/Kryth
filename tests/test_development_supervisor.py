import json

import pytest

from utils.development_supervisor import DevelopmentSupervisor, PHASES


def test_supervisor_persists_and_restores_checkpoint(tmp_path):
    first = DevelopmentSupervisor(tmp_path)
    first.start_phase("backend_development")
    first.plan_task("run backend tests")
    first.record_command("pytest -q", tmp_path, 0)
    first.record_file_change(tmp_path / "app.py")
    first.record_verification("backend tests", True, "12 passed")

    restored = DevelopmentSupervisor(tmp_path)
    assert restored.state["current_phase"] == "backend_development"
    assert restored.state["current_task"] == "run backend tests"
    assert restored.state["commands_executed"][-1]["exit_code"] == 0
    assert str(tmp_path / "app.py") in restored.state["files_changed"]
    assert restored.state["last_successful_verification"]["description"] == "backend tests"
    assert set(restored.state["phases"]) == set(PHASES)
    assert "run backend tests" in restored.resume_summary()


def test_supervisor_requires_verification_to_complete_task(tmp_path):
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.plan_task("implement feature")
    with pytest.raises(ValueError, match="verification"):
        supervisor.complete_task("implement feature", "")


def test_supervisor_redacts_common_inline_secrets(tmp_path):
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.record_command("tool --api-key=abc123", tmp_path, 1)
    persisted = json.loads(supervisor.path.read_text(encoding="utf-8"))
    assert "abc123" not in json.dumps(persisted)
    assert "[redacted]" in persisted["commands_executed"][0]["command"]


def test_task_checkpoint_persists_steps_and_refuses_incomplete_completion(tmp_path):
    supervisor = DevelopmentSupervisor(tmp_path)
    task_id = supervisor.begin_task("Migrate the module")
    supervisor.add_task_step("inspect", "Inspect current API")
    supervisor.add_task_step("migrate", "Update imports")

    restored = DevelopmentSupervisor(tmp_path)
    checkpoint = restored.state["task_checkpoint"]
    assert checkpoint["task_id"] == task_id
    assert checkpoint["status"] == "in_progress"
    assert checkpoint["pending_steps"] == ["Inspect current API", "Update imports"]
    assert "in progress" in (restored.resume_summary() or "")
    with pytest.raises(ValueError, match="pending or failed"):
        restored.complete_task_checkpoint()

    restored.verify_task_step("inspect", "Read API definition and call sites")
    assert restored.state["task_checkpoint"]["status"] == "in_progress"
    restored.verify_task_step("migrate", "Import check passed")
    assert restored.state["task_checkpoint"]["status"] == "completed"
    restored.complete_task_checkpoint()


def test_failed_task_step_blocks_task_completion_and_keeps_recovery(tmp_path):
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Repair browser")
    supervisor.add_task_step("navigate", "Navigate to target")
    supervisor.fail_task_step("navigate", "Connection lost", "Reconnect and inspect current tab")
    with pytest.raises(ValueError, match="pending or failed"):
        supervisor.complete_task_checkpoint()
    checkpoint = DevelopmentSupervisor(tmp_path).state["task_checkpoint"]
    assert checkpoint["failed_steps"] == ["Navigate to target"]
    assert checkpoint["recovery_information"] == "Reconnect and inspect current tab"
