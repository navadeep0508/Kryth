"""Runtime v2 — KRYTH_ADAPTER_STREAM promotion gate.

Encodes the promotion criteria as a pure, deterministic evaluator so that
"is the adapter path ready to become the default?" is an objective pass/fail
over collected burn-in data — never a judgement call.

This module NEVER promotes anything. It does not read or write the feature
flag, touch config, or change runtime behavior. It only evaluates a
``BurnInReport`` and returns a ``GateResult``. Promotion (Phase A) is a separate,
human-gated action taken only after this gate passes on LIVE multi-provider data.

Typical use after a live burn-in:
    from agent.model.promotion_gate import evaluate_promotion, BurnInReport
    result = evaluate_promotion(report)
    print(result.render())
    if result.passed:
        ...  # a human may now promote the flag (Phase A)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


# ── Criteria constants (from the promotion spec) ─────────────────────────────

REQUIRED_PROVIDERS: Tuple[str, ...] = (
    "openai", "anthropic", "deepseek", "qwen", "nvidia", "gpt-oss",
)

# Metrics that MUST have been collected (present in the telemetry snapshot)
# before any promotion decision can be made.
REQUIRED_METRICS: Tuple[str, ...] = (
    "adapter_used", "fallback_activated", "parser_error", "malformed_chunk",
    "tool_normalization", "harmony_sanitization", "retry", "stream_completion",
    "ttft",
)

# Parity thresholds.
TOOL_DISPATCH_PARITY_MIN = 0.999
CONTENT_PARITY_MIN = 0.999
FINISH_REASON_PARITY_MIN = 1.0
USAGE_PARITY_MIN = 1.0


@dataclass
class BurnInReport:
    """Structured input to the gate, assembled from a live burn-in run.

    Parity ratios are in [0, 1]. Counts are absolute. Booleans are regression
    gates from the surrounding test infrastructure.
    """
    providers_seen: set = field(default_factory=set)
    metrics_collected: set = field(default_factory=set)

    tool_dispatch_parity: float = 0.0
    content_parity: float = 0.0
    finish_reason_parity: float = 0.0
    usage_parity: float = 0.0

    parser_errors: int = 0
    malformed_chunk_regressions: int = 0
    harmony_sanitization_failures: int = 0
    unexpected_fallback_activations: int = 0
    stream_completion_failures: int = 0

    frozen_contracts_green: bool = False
    mcp_green: bool = False
    event_ordering_green: bool = False

    # Sample size — a guard against declaring victory on too little data.
    total_turns: int = 0
    min_turns: int = 100


@dataclass
class GateResult:
    criteria: List[Tuple[str, bool, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.criteria) and bool(self.criteria)

    def failures(self) -> List[Tuple[str, bool, str]]:
        return [c for c in self.criteria if not c[1]]

    def render(self) -> str:
        lines = ["KRYTH_ADAPTER_STREAM PROMOTION GATE", "=" * 52]
        for name, ok, detail in self.criteria:
            mark = "PASS" if ok else "FAIL"
            lines.append(f"  [{mark}] {name:34} {detail}")
        lines.append("-" * 52)
        lines.append("VERDICT: " + ("READY TO PROMOTE" if self.passed
                                     else f"NOT READY — {len(self.failures())} unmet"))
        return "\n".join(lines)


def evaluate_promotion(report: BurnInReport) -> GateResult:
    """Evaluate a burn-in report against the promotion criteria. Pure function."""
    c: List[Tuple[str, bool, str]] = []

    # Coverage: all required live providers must have been exercised.
    missing_p = [p for p in REQUIRED_PROVIDERS if p not in report.providers_seen]
    c.append((
        "live providers covered",
        not missing_p,
        "all 6 present" if not missing_p else f"missing: {', '.join(missing_p)}",
    ))

    # Coverage: all required metrics collected.
    missing_m = [m for m in REQUIRED_METRICS if m not in report.metrics_collected]
    c.append((
        "metrics collected",
        not missing_m,
        "all present" if not missing_m else f"missing: {', '.join(missing_m)}",
    ))

    # Sample size.
    c.append((
        "sufficient sample size",
        report.total_turns >= report.min_turns,
        f"{report.total_turns} turns (need >= {report.min_turns})",
    ))

    # Parity thresholds.
    c.append((
        "tool dispatch parity >= 99.9%",
        report.tool_dispatch_parity >= TOOL_DISPATCH_PARITY_MIN,
        f"{report.tool_dispatch_parity * 100:.3f}%",
    ))
    c.append((
        "final content parity >= 99.9%",
        report.content_parity >= CONTENT_PARITY_MIN,
        f"{report.content_parity * 100:.3f}%",
    ))
    c.append((
        "finish reason parity == 100%",
        report.finish_reason_parity >= FINISH_REASON_PARITY_MIN,
        f"{report.finish_reason_parity * 100:.3f}%",
    ))
    c.append((
        "usage accounting parity == 100%",
        report.usage_parity >= USAGE_PARITY_MIN,
        f"{report.usage_parity * 100:.3f}%",
    ))

    # Zero-tolerance counters.
    c.append(("parser errors == 0", report.parser_errors == 0, str(report.parser_errors)))
    c.append(("malformed chunk regressions == 0",
              report.malformed_chunk_regressions == 0, str(report.malformed_chunk_regressions)))
    c.append(("harmony sanitization failures == 0",
              report.harmony_sanitization_failures == 0, str(report.harmony_sanitization_failures)))
    c.append(("unexpected fallback activation == 0",
              report.unexpected_fallback_activations == 0, str(report.unexpected_fallback_activations)))
    c.append(("stream completion failures == 0",
              report.stream_completion_failures == 0, str(report.stream_completion_failures)))

    # Regression gates.
    c.append(("no frozen contract regressions", report.frozen_contracts_green, "green" if report.frozen_contracts_green else "RED"))
    c.append(("no MCP regressions", report.mcp_green, "green" if report.mcp_green else "RED"))
    c.append(("no event ordering regressions", report.event_ordering_green, "green" if report.event_ordering_green else "RED"))

    return GateResult(criteria=c)


def report_from_telemetry(
    telemetry_snapshot: dict,
    parity: Dict[str, float],
    providers_seen,
    *,
    frozen_contracts_green: bool = False,
    mcp_green: bool = False,
    event_ordering_green: bool = False,
    total_turns: int = 0,
    min_turns: int = 100,
    unexpected_fallback_activations: int = 0,
    malformed_chunk_regressions: int | None = None,
    harmony_sanitization_failures: int = 0,
) -> BurnInReport:
    """Build a BurnInReport from a telemetry snapshot + parity ratios.

    `parity` keys: tool_dispatch, content, finish_reason, usage (ratios 0..1).
    Metrics are considered "collected" if the counter is present in the
    snapshot (TTFT collected if the ttft section is non-empty).
    """
    counters = telemetry_snapshot.get("counters", {})
    collected = set(counters.keys())
    if telemetry_snapshot.get("ttft"):
        collected.add("ttft")
    if malformed_chunk_regressions is None:
        malformed_chunk_regressions = int(counters.get("malformed_chunk", 0))
    return BurnInReport(
        providers_seen=set(providers_seen),
        metrics_collected=collected,
        tool_dispatch_parity=parity.get("tool_dispatch", 0.0),
        content_parity=parity.get("content", 0.0),
        finish_reason_parity=parity.get("finish_reason", 0.0),
        usage_parity=parity.get("usage", 0.0),
        parser_errors=int(counters.get("parser_error", 0)),
        malformed_chunk_regressions=malformed_chunk_regressions,
        harmony_sanitization_failures=harmony_sanitization_failures,
        unexpected_fallback_activations=unexpected_fallback_activations,
        stream_completion_failures=0,  # caller overrides if any clean-exit failures observed
        frozen_contracts_green=frozen_contracts_green,
        mcp_green=mcp_green,
        event_ordering_green=event_ordering_green,
        total_turns=total_turns,
        min_turns=min_turns,
    )
