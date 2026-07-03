"""Runs the KRYTH evaluation suite inside a given project root.

Gracefully handles the case where no evaluation suite is present —
returns an empty EvalResult so the lab can still function with
benchmark-only comparison.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("improvement_lab.evaluation_runner")

_DEFAULT_TIMEOUT_S = int(os.environ.get("KRYTH_LAB_EVAL_TIMEOUT", "7200"))


@dataclass
class EvalResult:
    """Structured result from one evaluation run."""
    run_id: str = ""
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate_pct: float = 0.0
    avg_score: float = 0.0
    category_scores: dict[str, float] = field(default_factory=dict)
    timestamp: str = ""
    available: bool = False    # False = evaluation suite not found / skipped

    @property
    def failed_count(self) -> int:
        return self.failed or max(0, self.total_tests - self.passed)


_EMPTY = EvalResult(available=False)


class EvaluationRunner:
    """Executes the evaluation suite and returns an EvalResult."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()

    # ── Public API ────────────────────────────────────────────────────────────

    def run_baseline(self) -> EvalResult:
        return self._run(self.project_root, label="baseline")

    def run_experiment(self, sandbox_path: Path) -> EvalResult:
        return self._run(sandbox_path, label="experiment")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self, root: Path, label: str) -> EvalResult:
        runner = self._find_runner(root)
        if runner is None:
            logger.info(f"No evaluation suite found in {root} — skipping ({label})")
            return _EMPTY

        history_dir = root / "evaluation_history"
        history_dir.mkdir(exist_ok=True)
        before = set(history_dir.glob("*.json"))

        logger.info(f"Starting evaluation ({label}) in {root} …")
        t0 = time.monotonic()

        try:
            result = subprocess.run(
                [sys.executable, str(runner.relative_to(root))],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=_DEFAULT_TIMEOUT_S,
                env={**os.environ, "PYTHONPATH": str(root / "kryth" / "src")},
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"Evaluation ({label}) timed out after {_DEFAULT_TIMEOUT_S}s")
            return _EMPTY
        except Exception as exc:
            logger.warning(f"Evaluation ({label}) failed to start: {exc}")
            return _EMPTY

        elapsed = time.monotonic() - t0
        logger.info(f"Evaluation ({label}) finished in {elapsed:.0f}s (exit={result.returncode})")

        # Try to parse result JSON
        after = set(history_dir.glob("*.json"))
        new_files = sorted(after - before, key=lambda p: p.stat().st_mtime)

        if not new_files:
            # Try to parse stdout
            return self._parse_stdout(result.stdout) or _EMPTY

        return self._parse_json(new_files[-1])

    def _find_runner(self, root: Path) -> Path | None:
        """Locate the evaluation entry point."""
        candidates = [
            root / "evaluation" / "run_evaluation.py",
            root / "evaluation" / "main.py",
            root / "evaluation" / "__main__.py",
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def _parse_json(self, path: Path) -> EvalResult:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return EvalResult(
                run_id=data.get("run_id", path.stem),
                total_tests=data.get("total_tests", 0),
                passed=data.get("passed", 0),
                failed=data.get("failed", 0),
                pass_rate_pct=data.get("pass_rate_pct", 0.0),
                avg_score=data.get("avg_score", 0.0),
                category_scores=data.get("category_scores", {}),
                timestamp=data.get("timestamp", ""),
                available=True,
            )
        except Exception as exc:
            logger.warning(f"Failed to parse evaluation JSON {path}: {exc}")
            return _EMPTY

    def _parse_stdout(self, stdout: str) -> EvalResult | None:
        """Best-effort stdout parsing for common formats."""
        try:
            for line in stdout.splitlines():
                if line.strip().startswith("{"):
                    data = json.loads(line.strip())
                    if "pass_rate_pct" in data or "passed" in data:
                        return EvalResult(
                            total_tests=data.get("total_tests", 0),
                            passed=data.get("passed", 0),
                            pass_rate_pct=data.get("pass_rate_pct", 0.0),
                            available=True,
                        )
        except Exception:
            pass
        return None
