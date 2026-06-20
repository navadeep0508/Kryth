"""Live terminal dashboard overlay for the evaluation pipeline.

Shows reviewer agent progress and per-dimension scores as they arrive.
Uses ANSI escape codes — no curses dependency.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

from .evaluation_metrics import EvaluationResult, ReviewScore


# ── ANSI ──────────────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_MAGENTA = "\033[35m"
_CLEAR_LINE = "\033[2K\r"


def _c(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}"


def _score_color(s: int) -> str:
    if s >= 80: return _GREEN
    if s >= 60: return _YELLOW
    return _RED


def _score_str(s: int) -> str:
    return _c(f"{s:3d}", _score_color(s))


def _bar(score: int, width: int = 10) -> str:
    filled = max(0, min(width, score * width // 100))
    return (
        _c("█" * filled, _score_color(score))
        + _c("░" * (width - filled), _DIM)
    )


_REVIEWER_LABELS = {
    "architecture":       "Architecture     ",
    "code_quality":       "Code Quality     ",
    "testing":            "Testing          ",
    "performance":        "Performance      ",
    "security":           "Security         ",
    "maintainability":    "Maintainability  ",
    "documentation":      "Documentation    ",
    "parallel_efficiency":"Parallel Eff.    ",
}


class EvaluationDashboard:
    """Prints live evaluation progress to stderr.

    Usage::
        dash = EvaluationDashboard(mission_ids=["M1", "M2"])
        dash.start()
        dash.mark_evaluating("M1")
        dash.update_reviewer("M1", "architecture", ReviewScore(...))
        dash.mark_done("M1", result)
        dash.stop()
    """

    def __init__(self, mission_ids: list[str], refresh_hz: float = 2.0):
        self._ids = mission_ids
        self._period = 1.0 / refresh_hz
        # Per-mission state
        self._results: dict[str, Optional[EvaluationResult]] = {m: None for m in mission_ids}
        self._reviewer_scores: dict[str, dict[str, ReviewScore]] = {m: {} for m in mission_ids}
        self._running: set[str] = set()
        self._done: set[str] = set()
        self._start_times: dict[str, float] = {}
        self._lock = threading.Lock()
        # Render state
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lines_printed = 0
        self._global_start = 0.0

    def start(self) -> None:
        self._global_start = time.monotonic()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._render(final=True)
        sys.stderr.write("\n")
        sys.stderr.flush()

    def mark_evaluating(self, mission_id: str) -> None:
        with self._lock:
            self._running.add(mission_id)
            self._start_times[mission_id] = time.monotonic()

    def update_reviewer(self, mission_id: str, dimension: str, rs: ReviewScore) -> None:
        with self._lock:
            self._reviewer_scores[mission_id][dimension] = rs

    def mark_done(self, mission_id: str, result: EvaluationResult) -> None:
        with self._lock:
            self._running.discard(mission_id)
            self._done.add(mission_id)
            self._results[mission_id] = result

    # ── Rendering ──────────────────────────────────────────────────────────────

    def _render(self, final: bool = False) -> None:
        with self._lock:
            running = set(self._running)
            done = set(self._done)
            results = dict(self._results)
            rev_scores = {k: dict(v) for k, v in self._reviewer_scores.items()}

        if self._lines_printed > 0:
            sys.stderr.write(f"\033[{self._lines_printed}A")

        total_elapsed = time.monotonic() - self._global_start
        out = []

        # Header
        total_done = len(done)
        avg_overall = 0.0
        if done:
            overalls = [
                (results[mid].scores.overall if results.get(mid) else 0)
                for mid in done
            ]
            avg_overall = sum(overalls) / len(overalls)

        header = (
            f"{_BOLD}KRYTH Evaluation{_RESET}  "
            f"elapsed={total_elapsed:.0f}s  "
            f"done={total_done}/{len(self._ids)}  "
            f"avg={_score_str(int(avg_overall))}"
        )
        out.append(_CLEAR_LINE + header)

        for mid in self._ids:
            result = results.get(mid)
            is_running = mid in running
            is_done = mid in done
            reviewers = rev_scores.get(mid, {})

            if not is_running and not is_done:
                out.append(_CLEAR_LINE + f"  {_c('·', _DIM)} [{mid}]  pending")
                continue

            elapsed = (
                result.evaluation_duration_s if result else
                (time.monotonic() - self._start_times.get(mid, time.monotonic()))
            )

            status_char = (
                _c("⟳", _CYAN) if is_running else
                (_c("✓", _GREEN) if (result and result.scores.overall >= 70) else _c("✗", _RED))
            )

            overall = result.scores.overall if result else 0
            name = (result.mission_name if result else mid)[:25]

            out.append(
                _CLEAR_LINE +
                f"  {status_char} [{mid}] {name:<25}  "
                f"overall={_score_str(overall)}  "
                f"{_bar(overall)}  "
                f"{elapsed:.0f}s"
            )

            # Show reviewer progress
            for dim, label in _REVIEWER_LABELS.items():
                rs = reviewers.get(dim)
                if rs is not None:
                    reviewer_tag = _c(f"[{rs.reviewer[:3]}]", _DIM)
                    out.append(
                        _CLEAR_LINE +
                        f"      {label}  {_score_str(rs.score)}  {_bar(rs.score, 8)}  "
                        f"{reviewer_tag}"
                    )
                elif is_running:
                    out.append(
                        _CLEAR_LINE +
                        f"      {label}  {_c('   …', _DIM)}"
                    )

        output = "\n".join(out) + "\n"
        sys.stderr.write(output)
        sys.stderr.flush()
        self._lines_printed = len(out)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._render()
            self._stop_event.wait(timeout=self._period)
