"""Orchestrates the full Observe → Experiment → Benchmark → Recommend loop.

The LabManager is the single entry point for the Autonomous Improvement
Laboratory.  It coordinates all sub-components and enforces the invariant:

    KRYTH MUST NOT modify production code automatically.

Every run produces a LabSession with a MergeRecommendation that requires
explicit human approval. The lab only observes, experiments, and recommends.

Usage::

    from improvement_lab.lab_manager import LabManager

    lab = LabManager(project_root=".")

    # Run from the latest benchmark result
    session = lab.run_from_latest()

    if session.recommendation.recommend_merge:
        print("✓", session.recommendation.headline)
        print("Awaiting your approval — see", session.report_paths["merge_recommendation.md"])
    else:
        print("✗ Experiment rejected:", session.recommendation.reasoning)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from improvement_lab.experiment_generator import Experiment, ExperimentGenerator
from improvement_lab.branch_manager import BranchManager
from improvement_lab.sandbox_runner import SandboxRunner
from improvement_lab.benchmark_runner import BenchmarkRunner
from improvement_lab.evaluation_runner import EvaluationRunner, EvalResult
from improvement_lab.comparison_engine import ComparisonEngine, ComparisonResult
from improvement_lab.rollback_manager import RollbackManager
from improvement_lab.merge_recommender import MergeRecommender, MergeRecommendation
from improvement_lab.lab_report import LabReport

logger = logging.getLogger("improvement_lab.lab_manager")


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class LabConfig:
    """Runtime configuration for the lab."""
    run_evaluation: bool = True     # set False to skip eval suite (faster)
    keep_sandbox_on_success: bool = False  # destroy sandbox after successful run
    keep_sandbox_on_failure: bool = False  # destroy sandbox after failed run
    verbose: bool = True
    benchmark_timeout_s: int = 5400   # 90 min max for benchmark suite
    log_level: str = "INFO"


# ── Session ────────────────────────────────────────────────────────────────────

@dataclass
class LabSession:
    """Complete record of one experiment run through the lab."""
    session_id: str
    experiment: Experiment
    sandbox_branch: str
    sandbox_path: str
    status: str                          # "running" | "completed" | "failed" | "rolled_back"

    # Results (populated as the pipeline progresses)
    baseline_benchmark: object = None
    experimental_benchmark: object = None
    baseline_eval: Optional[EvalResult] = None
    experimental_eval: Optional[EvalResult] = None
    comparison: Optional[ComparisonResult] = None
    recommendation: Optional[MergeRecommendation] = None

    # Output file paths
    report_paths: dict[str, str] = field(default_factory=dict)

    # Bookkeeping
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at: Optional[str] = None
    error: Optional[str] = None


# ── Manager ────────────────────────────────────────────────────────────────────

class LabManager:
    """Coordinates the full self-improvement pipeline."""

    def __init__(
        self,
        project_root: str | Path = ".",
        config: Optional[LabConfig] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.config = config or LabConfig()

        logging.basicConfig(
            level=getattr(logging, self.config.log_level),
            format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        )

        # Add project kryth/src to path so benchmark imports resolve
        kryth_src = str(self.project_root / "kryth" / "src")
        if kryth_src not in sys.path:
            sys.path.insert(0, kryth_src)
        proj = str(self.project_root)
        if proj not in sys.path:
            sys.path.insert(0, proj)

        self._exp_gen     = ExperimentGenerator()
        self._branch_mgr  = BranchManager(self.project_root)
        self._sandbox     = SandboxRunner()
        self._bench       = BenchmarkRunner(self.project_root)
        self._eval        = EvaluationRunner(self.project_root)
        self._cmp_engine  = ComparisonEngine()
        self._rollback    = RollbackManager(self.project_root)
        self._recommender = MergeRecommender()
        self._reporter    = LabReport(self.project_root)

    # ── Public API ─────────────────────────────────────────────────────────────

    def run_from_latest(self) -> LabSession:
        """Load the most recent benchmark run, derive an experiment, and run the lab."""
        logger.info("Loading latest benchmark run …")
        bench_run = self._load_latest_benchmark()
        return self.run_from_benchmark(bench_run)

    def run_from_benchmark(self, bench_run) -> LabSession:
        """Derive recommendations from a BenchmarkRun and run the top experiment."""
        logger.info("Running optimization engine …")
        opt_result = self._run_optimization(bench_run)
        recs = list(opt_result.recommendations.sorted()) if opt_result else []

        if not recs:
            raise RuntimeError(
                "Optimization engine produced no recommendations. "
                "Run the benchmark first: python benchmark/run_benchmark.py"
            )

        logger.info(f"Top recommendation: [{recs[0].priority}] {recs[0].text}")

        experiment = self._exp_gen.generate(recs, self.project_root)
        if experiment is None:
            raise RuntimeError(
                f"No experiment template matched recommendation category '{recs[0].category}'. "
                "The target file fragment may have changed. Add a new template in "
                "improvement_lab/experiment_generator.py."
            )

        return self.run_experiment(experiment)

    def run_experiment(self, experiment: Experiment) -> LabSession:
        """Run the full pipeline for a given Experiment.

        Returns a completed LabSession regardless of success/failure.
        """
        session = LabSession(
            session_id=experiment.id,
            experiment=experiment,
            sandbox_branch=f"kryth-lab/{experiment.id}",
            sandbox_path=str(self._branch_mgr.sandbox_path(experiment.id)),
            status="running",
        )

        sandbox_path = None
        try:
            session, sandbox_path = self._pipeline(session)
        except KeyboardInterrupt:
            logger.warning("Lab interrupted by user — rolling back …")
            self._rollback.rollback(experiment, sandbox_path, reason="KeyboardInterrupt")
            session.status = "rolled_back"
            session.error = "Interrupted"
        except Exception as exc:
            logger.error(f"Lab pipeline failed: {exc}", exc_info=True)
            self._rollback.rollback(experiment, sandbox_path, reason=str(exc))
            session.status = "failed"
            session.error = str(exc)

        session.ended_at = datetime.now(timezone.utc).isoformat()

        # Always write the report so there's a record
        try:
            session.report_paths = self._reporter.generate_all(session)
            logger.info(f"Reports: {list(session.report_paths.values())}")
        except Exception as exc:
            logger.warning(f"Report generation failed: {exc}")

        if self.config.verbose:
            self._print_summary(session)

        return session

    # ── Pipeline ────────────────────────────────────────────────────────────────

    def _pipeline(self, session: LabSession):
        """Run the full pipeline. Returns (session, sandbox_path) on success."""
        exp = session.experiment

        # Step 1 — Baseline benchmark
        logger.info("Step 1/7: Running BASELINE benchmark …")
        session.baseline_benchmark = self._bench.run_baseline()

        # Step 2 — Create sandbox
        logger.info("Step 2/7: Creating sandbox worktree …")
        sandbox_path = self._branch_mgr.create_sandbox(exp.id)

        # Step 3 — Apply changes
        logger.info(f"Step 3/7: Applying {len(exp.changes)} change(s) to sandbox …")
        self._sandbox.apply(sandbox_path, exp.changes)

        # Step 4 — Experimental benchmark
        logger.info("Step 4/7: Running EXPERIMENTAL benchmark …")
        exp_bench = BenchmarkRunner(sandbox_path)
        session.experimental_benchmark = exp_bench.run_experiment(sandbox_path)

        # Step 5 — Evaluations (optional)
        if self.config.run_evaluation:
            logger.info("Step 5/7: Running evaluations …")
            session.baseline_eval = self._eval.run_baseline()
            exp_eval = EvaluationRunner(sandbox_path)
            session.experimental_eval = exp_eval.run_experiment(sandbox_path)
        else:
            logger.info("Step 5/7: Evaluation skipped (run_evaluation=False)")

        # Step 6 — Compare
        logger.info("Step 6/7: Comparing baseline vs experimental …")
        session.comparison = self._cmp_engine.compare(
            session.baseline_benchmark,
            session.experimental_benchmark,
            session.baseline_eval,
            session.experimental_eval,
        )

        # Step 7 — Recommend
        logger.info("Step 7/7: Generating merge recommendation …")
        calibrated_conf = self._rollback.calibrated_confidence(
            exp.category, exp.confidence
        )
        session.recommendation = self._recommender.recommend(
            exp, session.comparison, calibrated_confidence=calibrated_conf
        )

        # Record outcome
        actual_gain = session.recommendation.actual_gain_pct
        outcome = "success" if session.recommendation.recommend_merge else "rejected"
        self._rollback.record_lesson(exp, outcome, actual_gain)

        # Clean up sandbox (unless user wants to keep it)
        keep = (
            self.config.keep_sandbox_on_success
            if outcome == "success"
            else False
        )
        if not keep:
            try:
                self._branch_mgr.destroy_sandbox(exp.id)
                logger.info(f"Sandbox {exp.id} destroyed")
            except Exception as exc:
                logger.warning(f"Sandbox cleanup failed (non-fatal): {exc}")

        session.status = "completed"
        return session, sandbox_path

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _load_latest_benchmark(self):
        history = self.project_root / "benchmark_history"
        if not history.exists():
            raise RuntimeError(
                "benchmark_history/ not found. "
                "Run the benchmark first: python benchmark/run_benchmark.py"
            )
        files = sorted(history.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            raise RuntimeError("No benchmark runs found in benchmark_history/")

        from benchmark.benchmark_storage import load_run  # type: ignore
        return load_run(str(files[-1]))

    def _run_optimization(self, bench_run):
        try:
            from optimization import optimize_run  # type: ignore
            return optimize_run(bench_run)
        except ImportError:
            raise RuntimeError(
                "optimization/ module not found. "
                "Ensure the Phase 4 self-optimization engine is installed."
            )

    def _print_summary(self, session: LabSession) -> None:
        rec = session.recommendation
        sep = "=" * 72
        print(f"\n{sep}")
        print(f"  KRYTH Improvement Laboratory - {session.experiment.id}")
        print(sep)
        print(f"  Status    : {session.status.upper()}")
        if rec:
            verdict = "[RECOMMEND MERGE]" if rec.recommend_merge else "[REJECT]"
            print(f"  Verdict   : {verdict}")
            print(f"  Confidence: {rec.confidence:.2f}")
            print(f"  Expected  : +{rec.expected_gain_pct:.0f}%  Actual: {rec.actual_gain_pct:+.1f}%")
        if session.report_paths:
            print(f"\n  Reports:")
            for name, path in session.report_paths.items():
                print(f"    {name}")
        if rec:
            print(f"\n  Recommendation:")
            for line in rec.reasoning.splitlines()[-8:]:
                print(f"    {line}")
        print(sep + "\n")


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    """Run the lab from the command line."""
    import argparse

    parser = argparse.ArgumentParser(
        description="KRYTH Autonomous Improvement Laboratory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", default=".", help="Project root (default: .)")
    parser.add_argument("--no-eval", action="store_true", help="Skip evaluation suite")
    parser.add_argument("--keep-sandbox", action="store_true", help="Keep sandbox after run")
    parser.add_argument("--quiet", action="store_true", help="Suppress summary output")
    args = parser.parse_args()

    config = LabConfig(
        run_evaluation=not args.no_eval,
        keep_sandbox_on_success=args.keep_sandbox,
        verbose=not args.quiet,
    )

    lab = LabManager(project_root=args.root, config=config)
    session = lab.run_from_latest()
    sys.exit(0 if session.status == "completed" else 1)


if __name__ == "__main__":
    main()
