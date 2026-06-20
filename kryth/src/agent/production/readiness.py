"""Production readiness report + score (Phase 15) and self-optimization
recommendations (Phase 12).

`readiness_report(...)` synthesizes the production signals — token/context
reduction, worker utilization, mission throughput, recovery/checkpoint success,
parallel efficiency, bottleneck stats — into a single Markdown report plus a
0–100 **Production Readiness Score**.

`top_improvements(...)` is the self-optimization loop's output (Benchmark →
Optimization → Improvement Lab → Sandbox → Recommendation): the top N candidate
improvements with expected gain, confidence, and risk. It **never auto-merges** —
it only recommends; a human applies.

Pure: callers pass measured inputs; nothing here runs missions or mutates state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ReadinessInputs:
    # Reductions (Phase 1/2 + tool curation), 0–100 %.
    token_reduction_pct: float = 0.0
    context_reduction_pct: float = 0.0
    # Scheduling (dependency-aware, Phase 8/10), 0–100.
    worker_utilization_pct: float = 0.0
    parallel_efficiency_pct: float = 0.0
    idle_reduction_pct: float = 0.0
    # Reliability, 0–1.
    recovery_success_rate: float = 0.0
    checkpoint_success_rate: float = 0.0
    tool_success_rate: float = 0.0
    # Throughput.
    mission_throughput: float = 0.0     # missions/hour (informational)
    # Quality gates (booleans → contributes to score).
    contracts_green: bool = True
    adversarial_pass: bool = True
    suite_pass: bool = True


# Weighting of each dimension toward the 0–100 readiness score.
_WEIGHTS = {
    "efficiency": 0.20,     # token + context reduction
    "utilization": 0.20,    # worker utilization + parallel efficiency
    "reliability": 0.25,    # recovery + checkpoint + tool success
    "gates": 0.35,          # contracts + adversarial + suite (the hard gates)
}


@dataclass
class ReadinessScore:
    score: float
    grade: str
    dimensions: dict = field(default_factory=dict)
    risks: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"score": round(self.score, 1), "grade": self.grade,
                "dimensions": self.dimensions, "risks": self.risks,
                "limitations": self.limitations}


def _grade(score: float) -> str:
    if score >= 90:
        return "A — production ready"
    if score >= 80:
        return "B — production ready with monitoring"
    if score >= 70:
        return "C — staging ready"
    if score >= 50:
        return "D — pre-production"
    return "E — not ready"


def compute_score(inp: ReadinessInputs) -> ReadinessScore:
    efficiency = (min(100.0, inp.token_reduction_pct) + min(100.0, inp.context_reduction_pct)) / 2
    utilization = (min(100.0, inp.worker_utilization_pct) + min(100.0, inp.parallel_efficiency_pct)) / 2
    reliability = 100 * (inp.recovery_success_rate + inp.checkpoint_success_rate + inp.tool_success_rate) / 3
    gates = 100 * (int(inp.contracts_green) + int(inp.adversarial_pass) + int(inp.suite_pass)) / 3

    dims = {"efficiency": round(efficiency, 1), "utilization": round(utilization, 1),
            "reliability": round(reliability, 1), "gates": round(gates, 1)}
    score = sum(dims[k] * w for k, w in _WEIGHTS.items())

    risks, limitations = [], []
    if not inp.contracts_green:
        risks.append("Frozen contracts not green — DO NOT ship.")
    if not inp.adversarial_pass:
        risks.append("Adversarial gate failing — routing/safety regressions possible.")
    if inp.recovery_success_rate < 0.8:
        risks.append(f"Recovery success {inp.recovery_success_rate:.0%} < 80% — crash resilience unproven.")
    if inp.worker_utilization_pct < 60:
        limitations.append(f"Worker utilization {inp.worker_utilization_pct:.0f}% — parallel headroom unused.")
    if inp.mission_throughput == 0:
        limitations.append("Mission throughput not measured under sustained load (no long-run soak).")

    return ReadinessScore(score=score, grade=_grade(score), dimensions=dims,
                          risks=risks, limitations=limitations)


def readiness_report(inp: ReadinessInputs, *, title: str = "KRYTH Production Readiness") -> str:
    s = compute_score(inp)
    lines = [
        f"# {title}", "",
        f"**Production Readiness Score: {s.score:.1f}/100 — {s.grade}**", "",
        "## Dimension scores",
        f"- Efficiency (token/context reduction): {s.dimensions['efficiency']}/100",
        f"- Utilization (workers/parallel): {s.dimensions['utilization']}/100",
        f"- Reliability (recovery/checkpoint/tool): {s.dimensions['reliability']}/100",
        f"- Quality gates (contracts/adversarial/suite): {s.dimensions['gates']}/100",
        "",
        "## Performance metrics",
        f"- Token reduction: {inp.token_reduction_pct:.1f}%",
        f"- Context reduction: {inp.context_reduction_pct:.1f}%",
        f"- Worker utilization: {inp.worker_utilization_pct:.1f}%",
        f"- Parallel efficiency: {inp.parallel_efficiency_pct:.1f}%",
        f"- Idle reduction (dependency-aware): {inp.idle_reduction_pct:.1f}%",
        f"- Recovery success: {inp.recovery_success_rate:.0%}",
        f"- Checkpoint success: {inp.checkpoint_success_rate:.0%}",
        f"- Tool success rate: {inp.tool_success_rate:.0%}",
        f"- Mission throughput: {inp.mission_throughput:g}/hr",
        "",
    ]
    if s.risks:
        lines += ["## Remaining risks"] + [f"- {r}" for r in s.risks] + [""]
    if s.limitations:
        lines += ["## Known limitations"] + [f"- {l}" for l in s.limitations] + [""]
    return "\n".join(lines)


# ── Self-optimization recommendations (Phase 12) ─────────────────────────────

@dataclass
class Improvement:
    title: str
    expected_gain_pct: float
    confidence: float       # 0–1
    risk: str               # "low" | "medium" | "high"
    rationale: str = ""
    auto_merge: bool = False   # ALWAYS False — human approval required

    def to_dict(self) -> dict:
        return {"title": self.title, "expected_gain_pct": round(self.expected_gain_pct, 1),
                "confidence": round(self.confidence, 2), "risk": self.risk,
                "rationale": self.rationale, "auto_merge": False}


def top_improvements(inp: ReadinessInputs, *, n: int = 5) -> List[Improvement]:
    """Rank candidate improvements by expected gain × confidence ÷ risk.

    Never auto-merges (every Improvement.auto_merge is False). The Improvement
    Lab would sandbox-validate these before a human applies them."""
    risk_w = {"low": 1.0, "medium": 0.7, "high": 0.4}
    candidates: List[Improvement] = []

    if inp.worker_utilization_pct < 75:
        candidates.append(Improvement(
            "Enable adaptive parallelism by default",
            expected_gain_pct=min(40.0, 75 - inp.worker_utilization_pct),
            confidence=0.8, risk="low",
            rationale="Utilization below target; runtime worker scaling reclaims idle capacity."))
    if inp.token_reduction_pct < 70:
        candidates.append(Improvement(
            "Apply context sharding to all multi-domain missions",
            expected_gain_pct=min(30.0, 70 - inp.token_reduction_pct),
            confidence=0.75, risk="low",
            rationale="Per-domain shards cut tokens sent to each worker."))
    if inp.recovery_success_rate < 0.9:
        candidates.append(Improvement(
            "Checkpoint before every risky operation",
            expected_gain_pct=(0.9 - inp.recovery_success_rate) * 100,
            confidence=0.7, risk="medium",
            rationale="More frequent checkpoints raise resume-vs-restart success."))
    if inp.tool_success_rate < 0.95:
        candidates.append(Improvement(
            "Route to highest-reputation tool/executor",
            expected_gain_pct=(0.95 - inp.tool_success_rate) * 100,
            confidence=0.65, risk="low",
            rationale="Reputation routing avoids historically flaky tools."))
    if inp.parallel_efficiency_pct < 80:
        candidates.append(Improvement(
            "Dependency-aware work stealing for blocked workers",
            expected_gain_pct=min(35.0, 80 - inp.parallel_efficiency_pct),
            confidence=0.85, risk="low",
            rationale="Blocked workers steal ready work instead of idling."))
    # Always offer a quality option.
    candidates.append(Improvement(
        "Add long-run soak test (24h / 100-mission) to CI",
        expected_gain_pct=10.0, confidence=0.5, risk="medium",
        rationale="Memory/queue growth under sustained load is currently unmeasured."))

    candidates.sort(
        key=lambda i: i.expected_gain_pct * i.confidence * risk_w.get(i.risk, 0.5),
        reverse=True)
    return candidates[:n]
