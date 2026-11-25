import json

import pytest

from utils.development_supervisor import (
    DevelopmentSupervisor, PHASES,
    TASK_PENDING, TASK_RUNNING, TASK_COMPLETED_STATE, TASK_FAILED,
    TASK_BLOCKED, TASK_AWAITING_APPROVAL, TASK_VERIFICATION_REQUIRED,
    TASK_VERIFIED,
)


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
    assert checkpoint["status"] == TASK_RUNNING
    assert checkpoint["pending_steps"] == ["Inspect current API", "Update imports"]
    assert "in progress" in (restored.resume_summary() or "")
    with pytest.raises(ValueError, match="pending or failed"):
        restored.complete_task_checkpoint()

    restored.verify_task_step("inspect", "Read API definition and call sites")
    assert restored.state["task_checkpoint"]["status"] == TASK_RUNNING
    restored.verify_task_step("migrate", "Import check passed")
    assert restored.state["task_checkpoint"]["status"] == TASK_COMPLETED_STATE
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


# ---------------------------------------------------------------------------
# Benchmark phase tracking tests
# ---------------------------------------------------------------------------

def test_benchmark_phases_keep_task_incomplete_when_plan_steps_done(tmp_path):
    """Test 1: Mark internal plan 6/6 complete while benchmark phases remain incomplete.
    Overall task must remain incomplete."""
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Benchmark task")

    # Add 6 internal plan steps (implementation tasks)
    for i in range(6):
        supervisor.add_task_step(f"step_{i}", f"Internal step {i}")

    # Add 6 benchmark phases (user-requested work)
    for i in range(6):
        supervisor.add_benchmark_phase(f"benchmark.phase.{i+1:02d}", f"Benchmark phase {i+1}")

    # Complete all plan steps with verification
    for i in range(6):
        supervisor.verify_task_step(f"step_{i}", f"Evidence for step {i}")

    # Task should still be incomplete because benchmark phases are not verified
    checkpoint = supervisor.state["task_checkpoint"]
    assert checkpoint["status"] != "completed"
    assert supervisor.has_incomplete_benchmark_phases()

    # complete_task_checkpoint should refuse
    with pytest.raises(ValueError, match="benchmark phases remain unverified"):
        supervisor.complete_task_checkpoint()


def test_benchmark_phases_5_of_6_task_incomplete(tmp_path):
    """Test 2: Complete 5 of 6 benchmark phases. Overall task must remain incomplete."""
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Benchmark task")

    for i in range(6):
        pid = f"benchmark.phase.{i+1:02d}"
        supervisor.add_benchmark_phase(pid, f"Benchmark phase {i+1}")
        supervisor.start_benchmark_phase(pid)
        supervisor.complete_benchmark_phase(pid, f"Evidence for phase {i+1}")

    # Verify only 5 of 6 phases
    for i in range(5):
        supervisor.verify_benchmark_phase(f"benchmark.phase.{i+1:02d}", f"Verification {i+1}")

    progress = supervisor.get_benchmark_progress()
    assert progress["verified"] == 5
    assert progress["total"] == 6
    assert supervisor.has_incomplete_benchmark_phases()
    assert not supervisor.all_benchmark_phases_verified()


def test_benchmark_phases_all_completed_without_verification(tmp_path):
    """Test 3: Complete all phases without verification. Overall task must remain unverified."""
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Benchmark task")

    for i in range(6):
        pid = f"benchmark.phase.{i+1:02d}"
        supervisor.add_benchmark_phase(pid, f"Benchmark phase {i+1}")
        supervisor.start_benchmark_phase(pid)
        supervisor.complete_benchmark_phase(pid, f"Evidence for phase {i+1}")

    # All completed but none verified
    progress = supervisor.get_benchmark_progress()
    assert progress["completed"] == 6
    assert progress["verified"] == 0
    assert not supervisor.all_benchmark_phases_verified()

    # complete_task_checkpoint should refuse because phases aren't verified
    with pytest.raises(ValueError, match="benchmark phases remain unverified"):
        supervisor.complete_task_checkpoint()


def test_benchmark_phases_all_verified_task_can_complete(tmp_path):
    """Test 4: Verify all required phases. Overall task can become complete."""
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.begin_task("Benchmark task")
    supervisor.add_task_step("step_1", "Internal step")
    supervisor.verify_task_step("step_1", "Evidence for step 1")

    for i in range(3):
        pid = f"benchmark.phase.{i+1:02d}"
        supervisor.add_benchmark_phase(pid, f"Benchmark phase {i+1}")
        supervisor.start_benchmark_phase(pid)
        supervisor.complete_benchmark_phase(pid, f"Evidence {i+1}")
        supervisor.verify_benchmark_phase(pid, f"Verification {i+1}")

    assert supervisor.all_benchmark_phases_verified()

    # complete_task_checkpoint should succeed
    supervisor.complete_task_checkpoint()
    assert supervisor.state["task_checkpoint"]["status"] == "completed"


def test_benchmark_phases_persist_across_interruption(tmp_path):
    """Test 5: Interrupt KRYTH midway and resume. Previously verified phases remain verified."""
    supervisor = DevelopmentSupervisor(tmp_path)

    # Add and verify phase 1
    supervisor.add_benchmark_phase("benchmark.phase.01", "Phase 1")
    supervisor.complete_benchmark_phase("benchmark.phase.01", "Evidence 1")
    supervisor.verify_benchmark_phase("benchmark.phase.01", "Verification 1")

    # Add and complete (but not verify) phase 2
    supervisor.add_benchmark_phase("benchmark.phase.02", "Phase 2")
    supervisor.complete_benchmark_phase("benchmark.phase.02", "Evidence 2")

    # Restore from disk (simulating interruption/resume)
    restored = DevelopmentSupervisor(tmp_path)

    # Phase 1 should still be verified
    phase1 = restored.get_benchmark_phase("benchmark.phase.01")
    assert phase1 is not None
    assert phase1["status"] == "verified"
    assert phase1["evidence"] == "Evidence 1"

    # Phase 2 should still be completed but not verified
    phase2 = restored.get_benchmark_phase("benchmark.phase.02")
    assert phase2 is not None
    assert phase2["status"] == "completed"
    assert phase2["evidence"] == "Evidence 2"

    # Task should still be incomplete
    assert restored.has_incomplete_benchmark_phases()


def test_benchmark_phases_no_duplicates(tmp_path):
    """Test: Prevent duplicate phases and incorrect completion counts."""
    supervisor = DevelopmentSupervisor(tmp_path)

    supervisor.add_benchmark_phase("benchmark.phase.01", "Phase 1")
    # Adding the same phase ID again should be ignored
    supervisor.add_benchmark_phase("benchmark.phase.01", "Phase 1 duplicate")

    phases = supervisor.get_all_benchmark_phases()
    assert len(phases) == 1
    assert phases[0]["id"] == "benchmark.phase.01"
    assert phases[0]["description"] == "Phase 1"

    progress = supervisor.get_benchmark_progress()
    assert progress["total"] == 1
    assert progress["pending"] == 1


def test_benchmark_phase_requires_evidence_to_complete(tmp_path):
    """A benchmark phase cannot be completed without execution evidence."""
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.add_benchmark_phase("benchmark.phase.01", "Phase 1")

    with pytest.raises(ValueError, match="evidence"):
        supervisor.complete_benchmark_phase("benchmark.phase.01", "")

    # Phase should still be pending
    phase = supervisor.get_benchmark_phase("benchmark.phase.01")
    assert phase["status"] == "pending"


def test_benchmark_phase_cannot_verify_without_completion(tmp_path):
    """A benchmark phase must be completed before it can be verified."""
    supervisor = DevelopmentSupervisor(tmp_path)
    supervisor.add_benchmark_phase("benchmark.phase.01", "Phase 1")

    with pytest.raises(ValueError, match="not completed"):
        supervisor.verify_benchmark_phase("benchmark.phase.01", "Verification evidence")

    # Phase should still be pending
    phase = supervisor.get_benchmark_phase("benchmark.phase.01")
    assert phase["status"] == "pending"


def test_benchmark_phase_unknown_id_raises(tmp_path):
    """Operations on unknown benchmark phase IDs should raise."""
    supervisor = DevelopmentSupervisor(tmp_path)

    with pytest.raises(ValueError, match="not found"):
        supervisor.start_benchmark_phase("nonexistent.phase")

    with pytest.raises(ValueError, match="not found"):
        supervisor.complete_benchmark_phase("nonexistent.phase", "evidence")

    with pytest.raises(ValueError, match="not found"):
        supervisor.verify_benchmark_phase("nonexistent.phase", "evidence")

    with pytest.raises(ValueError, match="not found"):
        supervisor.fail_benchmark_phase("nonexistent.phase", "error", "recovery")

    with pytest.raises(ValueError, match="not found"):
        supervisor.skip_benchmark_phase("nonexistent.phase")

    assert supervisor.get_benchmark_phase("nonexistent.phase") is None
