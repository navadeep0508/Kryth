"""Unified production telemetry (Phase 14).

KRYTH already emits telemetry in several places (model, retrieval, runtime). This
module is a single *aggregation surface* that collects metrics across the
production domains — mission, agent, worker, tool, executor, verification,
recovery, optimization — and exports JSON / Markdown / a dashboard dict.

It is additive: subsystems (or tests) push metrics in; nothing is intercepted.
Counters, gauges, and timers are the three primitives. Thread-safe.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# The eight production telemetry domains (Phase 14).
DOMAINS = (
    "mission", "agent", "worker", "tool",
    "executor", "verification", "recovery", "optimization",
)


@dataclass
class Timer:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0


@dataclass
class DomainMetrics:
    counters: Dict[str, float] = field(default_factory=dict)
    gauges: Dict[str, float] = field(default_factory=dict)
    timers: Dict[str, Timer] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "counters": dict(self.counters),
            "gauges": dict(self.gauges),
            "timers": {k: {"count": t.count, "avg_ms": round(t.avg_ms, 1),
                           "max_ms": round(t.max_ms, 1)} for k, t in self.timers.items()},
        }


class Telemetry:
    """Single aggregation point for production metrics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._domains: Dict[str, DomainMetrics] = {d: DomainMetrics() for d in DOMAINS}
        self._started = time.monotonic()

    def _dom(self, domain: str) -> DomainMetrics:
        with self._lock:
            if domain not in self._domains:
                self._domains[domain] = DomainMetrics()
            return self._domains[domain]

    def incr(self, domain: str, name: str, by: float = 1.0) -> None:
        with self._lock:
            d = self._dom(domain)
            d.counters[name] = d.counters.get(name, 0.0) + by

    def gauge(self, domain: str, name: str, value: float) -> None:
        with self._lock:
            self._dom(domain).gauges[name] = value

    def timing(self, domain: str, name: str, ms: float) -> None:
        with self._lock:
            t = self._dom(domain).timers.setdefault(name, Timer())
            t.count += 1
            t.total_ms += ms
            t.max_ms = max(t.max_ms, ms)

    # -- convenience domain helpers -----------------------------------------

    def mission_done(self, *, success: bool, duration_ms: float) -> None:
        self.incr("mission", "completed" if success else "failed")
        self.timing("mission", "duration", duration_ms)

    def tool_call(self, name: str, *, success: bool, runtime_ms: float = 0.0) -> None:
        self.incr("tool", "calls")
        self.incr("tool", "success" if success else "failure")
        if runtime_ms:
            self.timing("tool", "runtime", runtime_ms)

    def recovery(self, *, succeeded: bool) -> None:
        self.incr("recovery", "attempts")
        self.incr("recovery", "succeeded" if succeeded else "failed")

    # -- export -------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_s": round(time.monotonic() - self._started, 1),
                "domains": {d: m.to_dict() for d, m in self._domains.items()},
            }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.snapshot(), indent=indent)

    def to_markdown(self) -> str:
        snap = self.snapshot()
        lines = [f"# KRYTH Telemetry", "", f"_uptime {snap['uptime_s']}s_", ""]
        for dom, m in snap["domains"].items():
            if not (m["counters"] or m["gauges"] or m["timers"]):
                continue
            lines.append(f"## {dom.title()}")
            for k, v in m["counters"].items():
                lines.append(f"- {k}: {v:g}")
            for k, v in m["gauges"].items():
                lines.append(f"- {k} (gauge): {v:g}")
            for k, t in m["timers"].items():
                lines.append(f"- {k}: avg {t['avg_ms']}ms, max {t['max_ms']}ms (n={t['count']})")
            lines.append("")
        return "\n".join(lines)

    def dashboard(self) -> dict:
        """Flat dict of headline numbers for the live dashboard footer."""
        s = self.snapshot()["domains"]
        def c(dom, name):
            return int(s.get(dom, {}).get("counters", {}).get(name, 0))
        tool_calls = c("tool", "calls")
        tool_ok = c("tool", "success")
        rec_att = c("recovery", "attempts")
        rec_ok = c("recovery", "succeeded")
        return {
            "missions_completed": c("mission", "completed"),
            "missions_failed": c("mission", "failed"),
            "tool_calls": tool_calls,
            "tool_success_rate": round(tool_ok / tool_calls, 3) if tool_calls else 0.0,
            "recoveries": rec_att,
            "recovery_rate": round(rec_ok / rec_att, 3) if rec_att else 0.0,
        }

    def export(self, path) -> None:
        from pathlib import Path
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.suffix == ".md":
                p.write_text(self.to_markdown(), encoding="utf-8")
            else:
                p.write_text(self.to_json(), encoding="utf-8")
        except Exception:
            pass


# Module-level default.
_DEFAULT: Optional[Telemetry] = None
_LOCK = threading.Lock()


def get_telemetry() -> Telemetry:
    global _DEFAULT
    with _LOCK:
        if _DEFAULT is None:
            _DEFAULT = Telemetry()
        return _DEFAULT
