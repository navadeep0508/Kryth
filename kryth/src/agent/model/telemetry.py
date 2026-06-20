"""Runtime burn-in telemetry — Runtime v2.

Lightweight, in-process instrumentation for comparing the flagged ModelAdapter
streaming path against the legacy inline parser during production burn-in.

Design constraints (from the burn-in directive):
- MUST NOT change user-visible behavior. Recording is a cheap dict/counter
  update; it never touches stdout, the event bus, or the response.
- Counters are always recordable but stay near-zero cost. Heavier sampling
  (per-provider TTFT, event-order traces) is gated behind
  ``KRYTH_RUNTIME_TELEMETRY`` so the default path pays nothing extra.
- No dependency on the rest of the runtime — pure stdlib, importable anywhere.

Usage:
    from agent.model import telemetry as tel
    tel.incr("adapter_used")
    tel.observe_ttft("openai", 0.42)
    snap = tel.snapshot()   # dict, safe to log / assert in tests
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List


def _rich_enabled() -> bool:
    val = os.environ.get("KRYTH_RUNTIME_TELEMETRY", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


@dataclass
class _Telemetry:
    """Mutable, thread-safe telemetry sink. One process-wide instance."""

    # ── Counters (always recorded; trivial cost) ────────────────────────────
    counters: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # ── Per-provider TTFT samples (seconds) — rich mode only ────────────────
    ttft: Dict[str, List[float]] = field(default_factory=lambda: defaultdict(list))

    # ── Event-ordering traces (sequence of normalized chunk types) ──────────
    # Capped to avoid unbounded growth during a long burn-in session.
    event_orders: List[List[str]] = field(default_factory=list)

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _max_orders: int = 200

    # ── Recording API ───────────────────────────────────────────────────────

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self.counters[name] += by

    def observe_ttft(self, provider: str, seconds: float) -> None:
        if not _rich_enabled():
            return
        with self._lock:
            self.ttft[provider or "unknown"].append(round(float(seconds), 4))

    def record_event_order(self, order: List[str]) -> None:
        if not _rich_enabled() or not order:
            return
        with self._lock:
            if len(self.event_orders) < self._max_orders:
                self.event_orders.append(list(order))

    # ── Reporting API ───────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        with self._lock:
            ttft_stats = {}
            for prov, samples in self.ttft.items():
                if samples:
                    ttft_stats[prov] = {
                        "n": len(samples),
                        "min": min(samples),
                        "max": max(samples),
                        "avg": round(sum(samples) / len(samples), 4),
                    }
            # Always report the full canonical counter vocabulary with 0
            # defaults. A counter that never fired (e.g. parser_error == 0) is
            # an EXPLICIT zero, not "uncollected" — promotion consumers rely on
            # this distinction (0 errors must help, not block, promotion).
            counters = {name: 0 for name in COUNTERS}
            counters.update(self.counters)
            return {
                "counters": counters,
                "ttft": ttft_stats,
                "event_orders": [list(o) for o in self.event_orders],
            }

    def reset(self) -> None:
        with self._lock:
            self.counters = defaultdict(int)
            self.ttft = defaultdict(list)
            self.event_orders = []


# Process-wide singleton.
_TEL = _Telemetry()


# ── Module-level convenience ─────────────────────────────────────────────────

def incr(name: str, by: int = 1) -> None:
    _TEL.incr(name, by)


def observe_ttft(provider: str, seconds: float) -> None:
    _TEL.observe_ttft(provider, seconds)


def record_event_order(order: List[str]) -> None:
    _TEL.record_event_order(order)


def snapshot() -> dict:
    return _TEL.snapshot()


def reset() -> None:
    _TEL.reset()


# Canonical counter names — used by the streaming path and the parity harness.
# Kept here so producers and consumers agree on the vocabulary.
COUNTERS = (
    "adapter_used",          # a stream parsed via the ModelAdapter seam
    "legacy_used",           # a stream parsed via the inline legacy parser
    "fallback_activated",    # tools schema rejected -> text-mode fallback
    "retry",                 # a stream-open retry (max_tokens / tools / content)
    "parser_error",          # adapter normalize_chunk raised
    "malformed_chunk",       # a non-empty chunk yielded nothing usable
    "tool_normalization",    # a tool call was normalized through the adapter
    "harmony_sanitization",  # a tool name had Harmony/special tokens stripped
    "stream_completion",     # a stream loop finished cleanly
)
