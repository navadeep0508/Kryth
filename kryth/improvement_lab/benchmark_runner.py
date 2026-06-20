"""Runs the KRYTH benchmark suite inside a given project root (production or sandbox).

The runner executes `python benchmark/run_benchmark.py --no-dashboard`
as a subprocess and parses the newest JSON file from benchmark_history/.
This ensures benchmark results are always produced by the actual agent code
in the target directory, never by mocked or patched imports.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("improvement_lab.benchmark_runner")

# Per-mission timeout * 8 missions + overhead
_DEFAULT_TIMEOUT_S = int(os.environ.get("KRYTH_LAB_BENCH_TIMEOUT", str(8 * 600 + 300)))


class BenchmarkRunnerError(RuntimeError):
    pass


class BenchmarkRunner:
    """Executes the benchmark suite and returns a BenchmarkRun data object."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    # ── Public API ────────────────────────────────────────────────────────────

    def run_baseline(self) -> "BenchmarkRun":
        """Run benchmark against the production code (main worktree)."""
        return self._run(self.project_root, label="baseline")

    def run_experiment(self, sandbox_path: Path) -> "BenchmarkRun":
        """Run benchmark against the sandbox worktree."""
        return self._run(sandbox_path, label="experiment")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self, root: Path, label: str) -> "BenchmarkRun":
        history_dir = root / "benchmark_history"
        history_dir.mkdir(exist_ok=True)

        # Snapshot existing files so we can identify the new one
        before = set(history_dir.glob("*.json"))

        logger.info(f"Starting benchmark ({label}) in {root} …")
        t0 = time.monotonic()

        result = subprocess.run(
            [sys.executable, "benchmark/run_benchmark.py", "--no-dashboard"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_DEFAULT_TIMEOUT_S,
            env={**os.environ, "PYTHONPATH": str(root / "kryth" / "src")},
        )

        elapsed = time.monotonic() - t0
        logger.info(
            f"Benchmark ({label}) finished in {elapsed:.0f}s "
            f"(exit={result.returncode})"
        )

        if result.returncode not in (0, 1):
            raise BenchmarkRunnerError(
                f"Benchmark process exited with code {result.returncode}.\n"
                f"stderr: {result.stderr[-2000:]}"
            )

        # Find the new JSON file
        after = set(history_dir.glob("*.json"))
        new_files = sorted(after - before, key=lambda p: p.stat().st_mtime)
        if not new_files:
            # Fall back to newest file overall
            all_files = sorted(history_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
            if not all_files:
                raise BenchmarkRunnerError(
                    f"No benchmark JSON found in {history_dir} after run."
                )
            new_files = [all_files[-1]]

        run_path = new_files[-1]
        logger.info(f"Loading benchmark result: {run_path.name}")

        return self._load_run(root, run_path)

    def _load_run(self, root: Path, run_path: Path) -> "BenchmarkRun":
        try:
            sys.path.insert(0, str(root / "kryth" / "src"))
            sys.path.insert(0, str(root))
            from benchmark.benchmark_storage import load_run  # type: ignore
            return load_run(str(run_path))
        except ImportError:
            # Fallback: return a minimal dict wrapper
            import json
            data = json.loads(run_path.read_text(encoding="utf-8"))
            return _DictBenchmarkRun(data)
        finally:
            sys.path = [p for p in sys.path
                        if p not in (str(root / "kryth" / "src"), str(root))]


class _DictBenchmarkRun:
    """Minimal BenchmarkRun wrapper when benchmark module is not importable."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name)
