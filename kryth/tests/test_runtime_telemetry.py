"""Runtime burn-in telemetry — unit contract.

Telemetry must be cheap, opt-in for rich sampling, and never affect behavior.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.model import telemetry as tel


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    tel.reset()
    monkeypatch.delenv("KRYTH_RUNTIME_TELEMETRY", raising=False)
    yield
    tel.reset()


def test_counters_always_record():
    tel.incr("adapter_used")
    tel.incr("adapter_used")
    tel.incr("tool_normalization", 3)
    snap = tel.snapshot()["counters"]
    assert snap["adapter_used"] == 2
    assert snap["tool_normalization"] == 3


def test_ttft_requires_rich_mode(monkeypatch):
    # Off by default → not recorded.
    tel.observe_ttft("openai", 0.5)
    assert tel.snapshot()["ttft"] == {}
    # Enabled → recorded with stats.
    monkeypatch.setenv("KRYTH_RUNTIME_TELEMETRY", "1")
    tel.observe_ttft("openai", 0.5)
    tel.observe_ttft("openai", 1.5)
    stats = tel.snapshot()["ttft"]["openai"]
    assert stats["n"] == 2
    assert stats["min"] == 0.5 and stats["max"] == 1.5 and stats["avg"] == 1.0


def test_event_order_requires_rich_mode(monkeypatch):
    tel.record_event_order(["text", "done"])
    assert tel.snapshot()["event_orders"] == []
    monkeypatch.setenv("KRYTH_RUNTIME_TELEMETRY", "1")
    tel.record_event_order(["text", "tool_call", "done"])
    assert tel.snapshot()["event_orders"] == [["text", "tool_call", "done"]]


def test_reset_clears_everything(monkeypatch):
    monkeypatch.setenv("KRYTH_RUNTIME_TELEMETRY", "1")
    tel.incr("retry")
    tel.observe_ttft("qwen", 0.2)
    tel.record_event_order(["text"])
    tel.reset()
    snap = tel.snapshot()
    # After reset, the canonical counters are reported as explicit zeros
    # (snapshot always carries the full vocabulary), and rich data is cleared.
    assert all(v == 0 for v in snap["counters"].values())
    assert snap["ttft"] == {}
    assert snap["event_orders"] == []


def test_canonical_counter_names_present():
    # The producer/consumer vocabulary is stable.
    for name in ("adapter_used", "legacy_used", "fallback_activated", "retry",
                 "parser_error", "malformed_chunk", "tool_normalization",
                 "harmony_sanitization", "stream_completion"):
        assert name in tel.COUNTERS


def test_snapshot_is_a_copy():
    tel.incr("adapter_used")
    snap = tel.snapshot()
    snap["counters"]["adapter_used"] = 999
    assert tel.snapshot()["counters"]["adapter_used"] == 1
