"""KRYTH Autonomous Self-Optimization Engine.

Measures execution, detects bottlenecks, and recommends improvements.
Never modifies code — only analyzes and reports.

Usage::

    from optimization import optimize_run
    result = optimize_run(run, previous_run=prev, output_path="report_opt.md")
    for rec in result.recommendations.sorted():
        print(f"[{rec.priority}] {rec.text}")
"""

from optimization.optimizer import optimize_run, OptimizationResult

__all__ = ["optimize_run", "OptimizationResult"]
