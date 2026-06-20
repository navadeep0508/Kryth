"""Mission Dashboard — live ASCII portfolio overview for the MOS."""

from __future__ import annotations

import threading
from typing import Any, Optional

from agent.mos.mission_state import Mission, MissionStatus

_portfolio: dict[str, Mission] = {}
_utilization: dict[str, float] = {}
_lock = threading.Lock()


def push_portfolio(portfolio: dict[str, Mission], utilization: dict[str, float]) -> None:
    global _portfolio, _utilization
    with _lock:
        _portfolio    = dict(portfolio)
        _utilization  = dict(utilization)


def render_portfolio_panel(width: int = 56) -> str:
    with _lock:
        portfolio    = dict(_portfolio)
        utilization  = dict(_utilization)

    sep   = "-" * width
    lines = [
        "=" * width,
        "  KRYTH MISSION OS — PORTFOLIO".center(width),
        "=" * width,
    ]

    if not portfolio:
        lines += ["  (no missions)", "=" * width]
        return "\n".join(lines)

    # Utilization bar
    w_pct = utilization.get("workers_pct", 0.0)
    t_pct = utilization.get("tokens_pct", 0.0)
    lines += [
        f"  Workers : {_bar(w_pct, 20)} {w_pct:4.0f}%",
        f"  Tokens  : {_bar(t_pct, 20)} {t_pct:4.0f}%",
        sep,
    ]

    # Group missions by status
    status_order = [
        MissionStatus.RUNNING, MissionStatus.PAUSED, MissionStatus.BLOCKED,
        MissionStatus.QUEUED,  MissionStatus.COMPLETED, MissionStatus.FAILED,
    ]
    status_icons = {
        MissionStatus.RUNNING:   "[>]",
        MissionStatus.PAUSED:    "[||]",
        MissionStatus.BLOCKED:   "[~]",
        MissionStatus.QUEUED:    "[ ]",
        MissionStatus.COMPLETED: "[OK]",
        MissionStatus.FAILED:    "[!]",
        MissionStatus.ARCHIVED:  "[A]",
    }

    shown = 0
    for status in status_order:
        bucket = [m for m in portfolio.values() if m.status == status]
        if not bucket:
            continue
        for m in bucket[:6]:  # cap per-status display
            icon   = status_icons.get(status, "[?]")
            name   = m.name[:20] if m.name else m.goal[:20]
            prog   = f"{m.metrics.progress_pct:4.0f}%"
            conf   = f"c={m.metrics.confidence:.0%}"
            line   = f"  {icon} {name:<20} {prog} {conf}"
            lines.append(line[:width])
            shown += 1
        if len(bucket) > 6:
            lines.append(f"       ... and {len(bucket)-6} more {status.value}")

    if not shown:
        lines.append("  (all missions archived)")

    # Summary counts
    def count(s): return sum(1 for m in portfolio.values() if m.status == s)
    lines += [
        sep,
        f"  R={count(MissionStatus.RUNNING)}  "
        f"Q={count(MissionStatus.QUEUED)}  "
        f"P={count(MissionStatus.PAUSED)}  "
        f"B={count(MissionStatus.BLOCKED)}  "
        f"OK={count(MissionStatus.COMPLETED)}  "
        f"F={count(MissionStatus.FAILED)}",
        "=" * width,
    ]
    return "\n".join(lines)


def get_portfolio() -> dict[str, Mission]:
    with _lock:
        return dict(_portfolio)


def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "#" * filled + "." * (width - filled)
