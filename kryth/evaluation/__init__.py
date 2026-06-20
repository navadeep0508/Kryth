"""KRYTH Autonomous Evaluation Framework.

Scores every mission on 12 dimensions: correctness, code quality,
architecture, testing, performance, security, maintainability,
documentation, parallel efficiency, memory reuse, recovery quality,
and an overall weighted score.

Usage (standalone)::
    python evaluation/run_evaluation.py --workspace /tmp/kryth_bm_M1_xxx
    python evaluation/run_evaluation.py --compare evaluation_history/eval_xxx.json

Usage (integrated with benchmark)::
    from evaluation import evaluate_mission, EvaluationResult
    result = evaluate_mission(workspace="/tmp/kryth_bm_M1_xxx",
                              mission_id="M1",
                              mission_passed=True)
    print(result.scores.overall)
"""

from .evaluation_metrics import (
    ReviewScore,
    EvaluationScores,
    RuleViolation,
    RuleReward,
    EvaluationResult,
    EvaluationRun,
)
from .quality_rules import analyze_workspace, security_score_from_violations
from .evaluator_agents import run_all_reviewers, ALL_REVIEWERS, REVIEWER_BY_DIMENSION
from .evaluation_runner import evaluate_mission, evaluate_benchmark_run
from .evaluation_storage import (
    save_evaluation_run,
    save_evaluation_result,
    load_evaluation_run,
    list_evaluation_runs,
    load_latest_evaluation_run,
    DEFAULT_EVAL_HISTORY_DIR,
)
from .evaluation_compare import (
    compare_evaluation_runs,
    EvalRunComparison,
    MissionEvalDiff,
)
from .evaluation_report import (
    generate_evaluation_markdown,
    generate_evaluation_text_summary,
    save_json_evaluation,
)
from .evaluation_dashboard import EvaluationDashboard

__all__ = [
    # metrics
    "ReviewScore",
    "EvaluationScores",
    "RuleViolation",
    "RuleReward",
    "EvaluationResult",
    "EvaluationRun",
    # core
    "analyze_workspace",
    "security_score_from_violations",
    "run_all_reviewers",
    "ALL_REVIEWERS",
    "REVIEWER_BY_DIMENSION",
    "evaluate_mission",
    "evaluate_benchmark_run",
    # storage
    "save_evaluation_run",
    "save_evaluation_result",
    "load_evaluation_run",
    "list_evaluation_runs",
    "load_latest_evaluation_run",
    "DEFAULT_EVAL_HISTORY_DIR",
    # compare
    "compare_evaluation_runs",
    "EvalRunComparison",
    "MissionEvalDiff",
    # report
    "generate_evaluation_markdown",
    "generate_evaluation_text_summary",
    "save_json_evaluation",
    # dashboard
    "EvaluationDashboard",
]
