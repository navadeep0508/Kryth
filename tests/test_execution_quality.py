from benchmarks.execution_quality import BenchmarkSample, calculate_metrics


def test_metrics_use_only_executed_cases_and_track_false_completion():
    metrics = calculate_metrics([
        BenchmarkSample(
            case_id="browser-navigation",
            executed=True,
            objective_met=True,
            verification_correct=True,
            recovery_attempted=False,
            duplicate_actions=0,
            false_completion=False,
            tokens=240,
            duration_seconds=1.5,
        ),
        BenchmarkSample(case_id="multi-step-recovery", executed=False),
        BenchmarkSample(
            case_id="test-task",
            executed=True,
            objective_met=False,
            verification_correct=False,
            recovery_attempted=True,
            recovery_succeeded=False,
            duplicate_actions=1,
            false_completion=True,
            tokens=160,
            duration_seconds=2.5,
        ),
    ])

    assert metrics["executed_cases"] == 2
    assert metrics["task_completion_rate"] == 0.5
    assert metrics["verification_accuracy"] == 0.5
    assert metrics["recovery_success_rate"] == 0.0
    assert metrics["duplicate_action_rate"] == 0.5
    assert metrics["false_completion_rate"] == 0.5
    assert metrics["total_tokens"] == 400
    assert metrics["average_execution_seconds"] == 2.0
