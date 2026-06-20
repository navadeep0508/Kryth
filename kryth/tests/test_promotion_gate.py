"""Runtime v2 — promotion gate contract.

The gate must enforce EVERY promotion criterion: it passes only when all are
met, and fails the moment any single criterion is violated. It must never
promote anything (no flag/config side effects).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.model.promotion_gate import (
    BurnInReport, evaluate_promotion, report_from_telemetry,
    REQUIRED_PROVIDERS, REQUIRED_METRICS,
)


def _clean_report(**overrides) -> BurnInReport:
    base = dict(
        providers_seen=set(REQUIRED_PROVIDERS),
        metrics_collected=set(REQUIRED_METRICS),
        tool_dispatch_parity=1.0,
        content_parity=1.0,
        finish_reason_parity=1.0,
        usage_parity=1.0,
        parser_errors=0,
        malformed_chunk_regressions=0,
        harmony_sanitization_failures=0,
        unexpected_fallback_activations=0,
        stream_completion_failures=0,
        frozen_contracts_green=True,
        mcp_green=True,
        event_ordering_green=True,
        total_turns=500,
    )
    base.update(overrides)
    return BurnInReport(**base)


def test_clean_report_passes():
    assert evaluate_promotion(_clean_report()).passed is True


# Each criterion, when violated, must fail the gate.

def test_missing_provider_fails():
    r = evaluate_promotion(_clean_report(providers_seen={"openai", "anthropic"}))
    assert r.passed is False
    assert any("providers" in name for name, ok, _ in r.failures())


def test_missing_metric_fails():
    r = evaluate_promotion(_clean_report(metrics_collected={"adapter_used"}))
    assert r.passed is False
    assert any("metrics" in name for name, ok, _ in r.failures())


def test_low_tool_dispatch_parity_fails():
    assert evaluate_promotion(_clean_report(tool_dispatch_parity=0.998)).passed is False
    # exactly the threshold passes
    assert evaluate_promotion(_clean_report(tool_dispatch_parity=0.999)).passed is True


def test_low_content_parity_fails():
    assert evaluate_promotion(_clean_report(content_parity=0.998)).passed is False


def test_finish_reason_must_be_perfect():
    assert evaluate_promotion(_clean_report(finish_reason_parity=0.999)).passed is False
    assert evaluate_promotion(_clean_report(finish_reason_parity=1.0)).passed is True


def test_usage_must_be_perfect():
    assert evaluate_promotion(_clean_report(usage_parity=0.999)).passed is False


@pytest.mark.parametrize("field", [
    "parser_errors", "malformed_chunk_regressions", "harmony_sanitization_failures",
    "unexpected_fallback_activations", "stream_completion_failures",
])
def test_zero_tolerance_counters(field):
    assert evaluate_promotion(_clean_report(**{field: 1})).passed is False
    assert evaluate_promotion(_clean_report(**{field: 0})).passed is True


@pytest.mark.parametrize("flag", [
    "frozen_contracts_green", "mcp_green", "event_ordering_green",
])
def test_regression_gates(flag):
    assert evaluate_promotion(_clean_report(**{flag: False})).passed is False


def test_insufficient_sample_fails():
    assert evaluate_promotion(_clean_report(total_turns=99, min_turns=100)).passed is False


def test_report_from_telemetry_roundtrip():
    # Error-type counters are collected but must be zero for a clean run.
    _zero = {"parser_error", "malformed_chunk"}
    snap = {
        "counters": {m: (0 if m in _zero else 1)
                     for m in REQUIRED_METRICS if m != "ttft"},
        "ttft": {"openai": {"n": 5, "avg": 0.3}},
    }
    parity = {"tool_dispatch": 1.0, "content": 1.0, "finish_reason": 1.0, "usage": 1.0}
    rep = report_from_telemetry(
        snap, parity, providers_seen=set(REQUIRED_PROVIDERS),
        frozen_contracts_green=True, mcp_green=True, event_ordering_green=True,
        total_turns=500,
    )
    assert "ttft" in rep.metrics_collected
    assert evaluate_promotion(rep).passed is True


def test_gate_has_no_side_effects(monkeypatch):
    # Evaluating the gate must not touch the feature flag or environment.
    import os
    before = os.environ.get("KRYTH_ADAPTER_STREAM")
    evaluate_promotion(_clean_report())
    assert os.environ.get("KRYTH_ADAPTER_STREAM") == before


def test_empty_report_does_not_pass():
    # A default (empty) report must never accidentally pass.
    assert evaluate_promotion(BurnInReport()).passed is False
