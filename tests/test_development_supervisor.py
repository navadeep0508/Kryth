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
