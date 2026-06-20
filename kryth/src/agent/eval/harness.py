"""SWE-bench-style benchmark harness for KRYTH.

Task format (YAML or dict):
  id: str                  unique task identifier
  description: str         natural-language task for the agent
  setup: list[str]         shell commands to run before the agent (create fixture files, etc.)
  validation: list[str]    shell commands that must exit 0 for "success"
  expected_files: list     files that must exist after the task
  expected_patch: str      optional unified diff to compare against (patch_correct metric)
  timeout_s: int           per-task wall-clock limit (default 120)
  category: str            bug_fix | refactor | creation | browser | long_horizon

Usage:
  harness = BenchmarkHarness()
  harness.load_tasks("kryth/src/agent/eval/tasks/")
  report = harness.run()
  harness.print_report(report)
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.eval.metrics import TaskMetrics, aggregate


@dataclass
class BenchmarkTask:
    id: str
    description: str
    category: str = "coding"
    setup: List[str] = field(default_factory=list)
    validation: List[str] = field(default_factory=list)
    expected_files: List[str] = field(default_factory=list)
    expected_patch: str = ""
    timeout_s: int = 120
    tags: List[str] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict) -> "BenchmarkTask":
        return BenchmarkTask(
            id=d["id"],
            description=d["description"],
            category=d.get("category", "coding"),
            setup=d.get("setup", []),
            validation=d.get("validation", []),
            expected_files=d.get("expected_files", []),
            expected_patch=d.get("expected_patch", ""),
            timeout_s=d.get("timeout_s", 120),
            tags=d.get("tags", []),
        )

    @staticmethod
    def from_yaml(path: str) -> "BenchmarkTask":
        try:
            import yaml
            with open(path) as f:
                return BenchmarkTask.from_dict(yaml.safe_load(f))
        except Exception as e:
            raise ValueError(f"Cannot load task {path}: {e}") from e

    @staticmethod
    def from_json(path: str) -> "BenchmarkTask":
        with open(path) as f:
            return BenchmarkTask.from_dict(json.load(f))


@dataclass
class BenchmarkResult:
    task: BenchmarkTask
    metrics: TaskMetrics
    agent_output: str = ""
    validation_output: str = ""


class BenchmarkHarness:
    """Run a suite of coding tasks through KRYTH and collect metrics.

    Provides two execution paths:
      run_with_agent(task, agent_fn)  — calls your agent function directly
      run_subprocess(task)            — spawns `kryth` CLI in a subprocess

    For CI / automated eval, use run_subprocess to isolate state.
    """

    def __init__(self, workdir: str = ".", verbose: bool = True) -> None:
        self._tasks: List[BenchmarkTask] = []
        self._workdir = workdir
        self._verbose = verbose

    # ── Task loading ──────────────────────────────────────────────────────────

    def add_task(self, task: BenchmarkTask) -> None:
        self._tasks.append(task)

    def load_tasks(self, directory: str) -> int:
        """Load all .yaml / .json task files from a directory. Returns count."""
        loaded = 0
        for p in sorted(Path(directory).rglob("*.yaml")) + list(Path(directory).rglob("*.json")):
            try:
                if p.suffix == ".yaml":
                    self._tasks.append(BenchmarkTask.from_yaml(str(p)))
                else:
                    self._tasks.append(BenchmarkTask.from_json(str(p)))
                loaded += 1
            except Exception as e:
                if self._verbose:
                    print(f"  [skip] {p.name}: {e}")
        return loaded

    def load_builtin_tasks(self) -> int:
        """Load the built-in task suite shipped with KRYTH."""
        here = Path(__file__).parent / "tasks"
        if not here.exists():
            return 0
        return self.load_tasks(str(here))

    # ── Execution ─────────────────────────────────────────────────────────────

    def run(
        self,
        agent_fn: Optional[Callable[[str], str]] = None,
        categories: Optional[List[str]] = None,
        max_tasks: int = 0,
    ) -> Dict[str, Any]:
        """Run all loaded tasks. Returns a report dict."""
        tasks = self._tasks
        if categories:
            tasks = [t for t in tasks if t.category in categories]
        if max_tasks:
            tasks = tasks[:max_tasks]

        results: List[BenchmarkResult] = []
        for i, task in enumerate(tasks, 1):
            if self._verbose:
                print(f"\n[{i}/{len(tasks)}] {task.id} ({task.category})")
            if agent_fn is not None:
                r = self.run_with_agent(task, agent_fn)
            else:
                r = self.run_subprocess(task)
            results.append(r)
            if self._verbose:
                m = r.metrics
                print(f"  score={m.score():.2f}  success={m.success}  "
                      f"compile={m.compile_ok}  tests={m.tests_pass}  "
                      f"retries={m.retries}  {m.duration_s:.1f}s")

        metrics = [r.metrics for r in results]
        agg = aggregate(metrics)
        report = {
            "summary": agg,
            "tasks": [r.metrics.to_dict() for r in results],
        }
        return report

    def run_with_agent(
        self,
        task: BenchmarkTask,
        agent_fn: Callable[[str], str],
    ) -> BenchmarkResult:
        """Run a single task by calling agent_fn(description) → output."""
        metrics = TaskMetrics(task_id=task.id)
        agent_output = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            self._run_setup(task, tmpdir, metrics)
            t0 = time.monotonic()
            try:
                agent_output = agent_fn(task.description)
                metrics.success = True
            except Exception as e:
                metrics.errors.append(f"agent exception: {e}")
            metrics.duration_s = time.monotonic() - t0

            val_out = self._run_validation(task, tmpdir, metrics)
            self._check_expected_files(task, tmpdir, metrics)
            if task.expected_patch:
                self._check_patch_correctness(task, tmpdir, metrics)
            os.chdir(self._workdir)

        metrics.output_summary = agent_output[:300]
        return BenchmarkResult(task=task, metrics=metrics,
                               agent_output=agent_output,
                               validation_output=val_out)

    def run_subprocess(self, task: BenchmarkTask) -> BenchmarkResult:
        """Run via `kryth` CLI in a subprocess — for isolated eval."""
        metrics = TaskMetrics(task_id=task.id)
        agent_output = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_setup(task, tmpdir, metrics)
            t0 = time.monotonic()
            try:
                result = subprocess.run(
                    ["kryth", "--non-interactive", task.description],
                    capture_output=True, text=True,
                    timeout=task.timeout_s, cwd=tmpdir,
                )
                agent_output = result.stdout + result.stderr
                metrics.success = result.returncode == 0
            except subprocess.TimeoutExpired:
                metrics.errors.append(f"timeout after {task.timeout_s}s")
            except FileNotFoundError:
                metrics.errors.append("kryth not installed (run: pip install -e kryth)")
            except Exception as e:
                metrics.errors.append(f"subprocess error: {e}")
            metrics.duration_s = time.monotonic() - t0

            self._run_validation(task, tmpdir, metrics)
            self._check_expected_files(task, tmpdir, metrics)
            if task.expected_patch:
                self._check_patch_correctness(task, tmpdir, metrics)

        metrics.output_summary = agent_output[:300]
        return BenchmarkResult(task=task, metrics=metrics, agent_output=agent_output)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _run_setup(self, task: BenchmarkTask, workdir: str, metrics: TaskMetrics) -> None:
        for cmd in task.setup:
            try:
                subprocess.run(cmd, shell=True, cwd=workdir,
                               capture_output=True, timeout=30)
            except Exception as e:
                metrics.errors.append(f"setup failed: {cmd!r}: {e}")

    def _run_validation(self, task: BenchmarkTask, workdir: str, metrics: TaskMetrics) -> str:
        if not task.validation:
            metrics.compile_ok = True
            metrics.tests_pass = True
            return ""
        outputs = []
        all_ok = True
        for cmd in task.validation:
            try:
                r = subprocess.run(cmd, shell=True, cwd=workdir,
                                   capture_output=True, text=True, timeout=60)
                outputs.append(r.stdout + r.stderr)
                if r.returncode != 0:
                    all_ok = False
                    metrics.errors.append(f"validation failed [{r.returncode}]: {cmd}")
            except subprocess.TimeoutExpired:
                all_ok = False
                metrics.errors.append(f"validation timeout: {cmd}")
            except Exception as e:
                all_ok = False
                metrics.errors.append(f"validation error: {e}")
        # Heuristic: if any "test" / "pytest" / "npm test" cmd, mark tests_pass
        test_cmds = [c for c in task.validation if any(
            k in c.lower() for k in ("test", "pytest", "jest", "mocha")
        )]
        compile_cmds = [c for c in task.validation if any(
            k in c.lower() for k in ("compile", "build", "tsc", "mypy", "check")
        )]
        metrics.tests_pass = all_ok if test_cmds else metrics.tests_pass
        metrics.compile_ok = all_ok if compile_cmds else all_ok
        return "\n".join(outputs)

    def _check_expected_files(self, task: BenchmarkTask, workdir: str, metrics: TaskMetrics) -> None:
        for f in task.expected_files:
            if not os.path.exists(os.path.join(workdir, f)):
                metrics.errors.append(f"missing expected file: {f}")
                metrics.success = False

    def _check_patch_correctness(self, task: BenchmarkTask, workdir: str, metrics: TaskMetrics) -> None:
        try:
            result = subprocess.run(
                ["git", "diff", "--unified=0"],
                capture_output=True, text=True, cwd=workdir,
            )
            actual_diff = result.stdout.strip()
            expected = task.expected_patch.strip()
            # Simple line-level overlap score
            exp_lines = set(expected.splitlines())
            act_lines = set(actual_diff.splitlines())
            if exp_lines:
                overlap = len(exp_lines & act_lines) / len(exp_lines)
                metrics.patch_correct = overlap >= 0.7
        except Exception:
            pass

    # ── Report ────────────────────────────────────────────────────────────────

    @staticmethod
    def print_report(report: dict) -> None:
        s = report.get("summary", {})
        print("\n" + "=" * 60)
        print("KRYTH BENCHMARK REPORT")
        print("=" * 60)
        print(f"Tasks:           {s.get('tasks', 0)}")
        print(f"Success rate:    {s.get('success_rate', 0):.1%}")
        print(f"Compile rate:    {s.get('compile_rate', 0):.1%}")
        print(f"Test pass rate:  {s.get('test_pass_rate', 0):.1%}")
        print(f"Avg score:       {s.get('avg_score', 0):.3f}")
        print(f"Avg retries:     {s.get('avg_retries', 0):.1f}")
        print(f"Avg LLM calls:   {s.get('avg_llm_calls', 0):.1f}")
        print(f"Avg tokens in:   {s.get('avg_tokens_in', 0):,}")
        print(f"Avg duration:    {s.get('avg_duration_s', 0):.1f}s")
        print("=" * 60)

    @staticmethod
    def save_report(report: dict, path: str = "kryth_benchmark.json") -> None:
        with open(path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to {path}")


def run_suite(
    agent_fn: Optional[Callable[[str], str]] = None,
    task_dir: Optional[str] = None,
    categories: Optional[List[str]] = None,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Convenience entry point: load built-in tasks + run."""
    h = BenchmarkHarness(verbose=verbose)
    if task_dir:
        h.load_tasks(task_dir)
    else:
        h.load_builtin_tasks()
    report = h.run(agent_fn=agent_fn, categories=categories)
    if verbose:
        BenchmarkHarness.print_report(report)
    return report
