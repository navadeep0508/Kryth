"""Live terminal dashboard overlay while benchmark missions run.

Uses simple ANSI escape codes — no curses dependency.
Renders a compact status table that refreshes every 0.5s.
"""

from __future__ import annotations

import threading
import time
import sys
from typing import Optional

from .benchmark_metrics import MissionMetrics


# ── ANSI helpers ──────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"
_CLEAR_LINE = "\033[2K\r"
_UP     = "\033[{n}A"


def _color(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}"


def _status_char(m: MissionMetrics, running: bool) -> str:
    if running:
        return _color("⟳", _CYAN)
    if m.success:
        return _color("✓", _GREEN)
    if m.error.startswith("TIMEOUT"):
        return _color("⏱", _YELLOW)
    return _color("✗", _RED)


def _bar(ratio: float, width: int = 10) -> str:
    filled = max(0, min(width, int(ratio * width)))
    return _color("█" * filled, _GREEN) + _color("░" * (width - filled), _DIM)


def _ms(v: float) -> str:
    if v < 0:
        return "   n/a"
    if v >= 60_000:
        return f"{v/60_000:5.1f}m"
    if v >= 1_000:
        return f"{v/1_000:5.1f}s"
    return f"{v:5.0f}ms"


# ── Dashboard state ───────────────────────────────────────────────────────────

class BenchmarkDashboard:
    """Prints a live-updating status table to stderr.

    Usage::
        dash = BenchmarkDashboard(mission_ids=["M1", "M2", "M3"])
        dash.start()
        dash.mark_running("M1")
        dash.update("M1", metrics)
        dash.mark_done("M1", metrics)
        dash.stop()
    """

    def __init__(self, mission_ids: list[str], refresh_hz: float = 2.0):
        self._ids = mission_ids
        self._period = 1.0 / refresh_hz
        self._metrics: dict[str, Optional[MissionMetrics]] = {mid: None for mid in mission_ids}
        self._running: set[str] = set()
        self._done: set[str] = set()
        self._start_times: dict[str, float] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lines_printed = 0

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

    def mark_running(self, mission_id: str) -> None:
        with self._lock:
            self._running.add(mission_id)
            self._start_times[mission_id] = time.monotonic()

    def update(self, mission_id: str, metrics: MissionMetrics) -> None:
        with self._lock:
            self._metrics[mission_id] = metrics

    def mark_done(self, mission_id: str, metrics: MissionMetrics) -> None:
        with self._lock:
            self._running.discard(mission_id)
            self._done.add(mission_id)
            self._metrics[mission_id] = metrics

    # ── Rendering ──────────────────────────────────────────────────────────────

    def _elapsed(self, mid: str) -> float:
        t = self._start_times.get(mid)
        return (time.monotonic() - t) * 1000.0 if t else 0.0

    def _render(self, final: bool = False) -> None:
        with self._lock:
            running = set(self._running)
            done = set(self._done)
            metrics = dict(self._metrics)

        # Move cursor up past previously printed lines
        if self._lines_printed > 0:
            sys.stderr.write(f"\033[{self._lines_printed}A")

        out = []
        elapsed_total = time.monotonic() - self._global_start
        passed = sum(1 for mid in done if (metrics.get(mid) or MissionMetrics()).success)
        total_done = len(done)

        header = (
            f"{_BOLD}KRYTH Benchmark{_RESET}  "
            f"elapsed={elapsed_total:.0f}s  "
            f"done={total_done}/{len(self._ids)}  "
            f"pass={passed}/{total_done}"
        )
        out.append(_CLEAR_LINE + header)

        for mid in self._ids:
            m = metrics.get(mid)
            is_running = mid in running
            is_done = mid in done

            if m is None:
                if is_running:
                    elapsed_ms = self._elapsed(mid)
                    row = (
                        f"  {_color('⟳', _CYAN)} [{mid}]  "
                        f"running  {_ms(elapsed_ms)}"
                    )
                else:
                    row = f"  {_color('·', _DIM)} [{mid}]  pending"
            else:
                status = _status_char(m, is_running)
                write_ms = m.timings.first_write_ms
                elapsed_ms = m.timings.duration_ms if is_done else self._elapsed(mid)
                par_pct = m.parallel.parallel_efficiency_pct
                tok = m.tokens_in + m.tokens_out
                row = (
                    f"  {status} [{mid}] {m.mission_name[:28]:<28}  "
                    f"dur={_ms(elapsed_ms)}  "
                    f"write@{_ms(write_ms)}  "
                    f"par={par_pct:.0f}%  "
                    f"tok={tok//1000}k"
                )
            out.append(_CLEAR_LINE + row)

        output = "\n".join(out) + "\n"
        sys.stderr.write(output)
        sys.stderr.flush()
        self._lines_printed = len(out)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._render()
            self._stop_event.wait(timeout=self._period)
