"""Executive Dashboard — live ASCII overlay for the Executive Intelligence Layer."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.executive.executive_state import (
    ExecutiveDecision, ExecutiveState, RiskLevel,
)


_state: Optional[ExecutiveState] = None
_lock  = threading.Lock()


def push_executive_state(state: ExecutiveState) -> None:
    """Replace the current snapshot with a fresh ExecutiveState."""
    global _state
    with _lock:
        _state = state


def render_executive_panel(width: int = 46) -> str:
    """Return an ASCII panel for embedding in the master dashboard."""
    with _lock:
        s = _state

    sep   = "-" * width
    lines = [
        "=" * width,
        "  KRYTH EXECUTIVE".center(width),
        "=" * width,
    ]

    if s is None:
        lines.append("  (idle)")
        lines.append("=" * width)
        return "\n".join(lines)

    conf_pct    = s.confidence.overall * 100
    qual_pct    = s.quality.overall * 100
    budget_pct  = s.budget.token_pct

    risk_icon   = {"low": "[OK]", "medium": "[!]", "high": "[!!]", "critical": "[!!!]"}
    rec_icon    = {
        "continue":  "[GO]",
        "pause":     "[||]",
        "replan":    "[~>]",
        "abort":     "[XX]",
        "escalate":  "[!!]",
        "spawn":     "[+W]",
        "retire":    "[-W]",
    }

    lines += [
        f"  Mission    : {s.mission[:width-15]}",
        sep,
        f"  Confidence : {conf_pct:5.1f}%",
        f"  Budget     : {budget_pct:5.1f}%  ({s.budget.tokens_used:,} tokens)",
        f"  Quality    : {qual_pct:5.1f}%",
        f"  Risk       : {risk_icon.get(s.risk.value,'[?]')} {s.risk.value.upper()}",
        sep,
        f"  Workers    : {s.team.active_workers} active  "
        f"{s.team.idle_workers} idle  "
        f"{s.team.busy_workers} busy",
        f"  Replans    : {s.health.replan_count}",
        f"  Escalations: {len(s.escalations)}",
        sep,
        f"  Recommend  : {rec_icon.get(s.recommendation.value,'[?]')} "
        f"{s.recommendation.value.upper()}",
    ]

    if s.rationale:
        lines.append(f"  Note       : {s.rationale[:width-14]}")

    if s.escalations:
        lines.append(sep)
        lines.append("  Escalations:")
        for esc in s.escalations[-3:]:
            lines.append(f"    - {esc[:width-6]}")

    lines.append("=" * width)
    return "\n".join(lines)


def get_executive_state() -> Optional[ExecutiveState]:
    with _lock:
        return _state
