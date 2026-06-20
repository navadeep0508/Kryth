"""Main optimizer — orchestrates all analyzers and produces OptimizationResult."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from benchmark.benchmark_metrics import BenchmarkRun
from benchmark.benchmark_compare import RunComparison, compare_runs

from optimization.execution_profiler import ExecutionProfile, profile_run
from optimization.bottleneck_detector import BottleneckReport, detect_bottlenecks
from optimization.worker_analyzer import WorkerAnalysis, analyze_workers
from optimization.memory_analyzer import MemoryAnalysis, analyze_memory
from optimization.llm_analyzer import LLMAnalysis, analyze_llm
from optimization.tool_analyzer import ToolAnalysis, analyze_tools
from optimization.scheduler_analyzer import SchedulerAnalysis, analyze_scheduler
from optimization.recommendation_engine import RecommendationSet, generate_recommendations
from optimization.optimization_report import generate_optimization_report


@dataclass
class OptimizationResult:
    run_id: str
    profile: ExecutionProfile
    bottlenecks: BottleneckReport
    workers: WorkerAnalysis
    memory: MemoryAnalysis
    llm: LLMAnalysis
    tools: ToolAnalysis
    scheduler: SchedulerAnalysis
    recommendations: RecommendationSet
    comparison: Optional[RunComparison] = None
    report_path: str = ""


def optimize_run(
    run: BenchmarkRun,
    previous_run: Optional[BenchmarkRun] = None,
    output_path: Optional[str] = None,
) -> OptimizationResult:
    """Run all analyzers over a BenchmarkRun and generate the engineering report.

    Args:
        run: The completed benchmark run to analyze.
        previous_run: Optional prior run for version comparison.
        output_path: If provided, write the Markdown report here.

    Returns:
        OptimizationResult with all analysis sub-results and the report text.
    """
    profile = profile_run(run)
    bottlenecks = detect_bottlenecks(run, profile)
    workers = analyze_workers(run)
    memory = analyze_memory(run)
    llm = analyze_llm(run)
    tools = analyze_tools(run)
    scheduler = analyze_scheduler(run)
    recs = generate_recommendations(profile, bottlenecks, workers, memory, llm, tools, scheduler)

    comparison: Optional[RunComparison] = None
    if previous_run is not None:
        comparison = compare_runs(previous_run, run)

    if output_path:
        generate_optimization_report(
            run, profile, bottlenecks, workers, memory, llm, tools, scheduler,
            recs, comparison=comparison, output_path=output_path,
        )

    return OptimizationResult(
        run_id=run.run_id,
        profile=profile,
        bottlenecks=bottlenecks,
        workers=workers,
        memory=memory,
        llm=llm,
        tools=tools,
        scheduler=scheduler,
        recommendations=recs,
        comparison=comparison,
        report_path=output_path or "",
    )
