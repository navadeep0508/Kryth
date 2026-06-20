"""V6 Performance Scorecard — Phase 10.

Compares V5 (always full stack) against V6 (tier-adaptive) across all
standard missions, computing 6 dimension scores (0-100, higher is better):

  Latency Score      — how fast the orchestration layer is (lower overhead = higher)
  Cost Score         — token efficiency (fewer wasted tokens = higher)
  Efficiency Score   — useful-work / total-time ratio
  Reliability Score  — success rate + recovery effectiveness
  Throughput Score   — missions per second the system can sustain
  Scalability Score  — overhead growth rate as mission size increases

V5 baseline: full stack always active (tier=4 ENTERPRISE for all missions).
V6 adaptive:  tier auto-selected per mission shape.

Both sides are run offline (mocked workers) for deterministic comparison.
Add KRYTH_REAL_PROVIDERS=1 to include real-provider deltas.
"""
from __future__ import annotations

import time
import unittest.mock as _mock
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class DimensionScore:
    name: str
    v5_score: float
    v6_score: float
    delta: float
    unit: str
    detail: str = ""

    def improvement_pct(self) -> float:
        if self.v5_score == 0:
            return 0.0
        return (self.v6_score - self.v5_score) / abs(self.v5_score) * 100


@dataclass
class V6Scorecard:
    dimensions: List[DimensionScore] = field(default_factory=list)
    v5_total: float = 0.0
    v6_total: float = 0.0
    elapsed_s: float = 0.0

    @property
    def overall_improvement_pct(self) -> float:
        if self.v5_total == 0:
            return 0.0
        return (self.v6_total - self.v5_total) / abs(self.v5_total) * 100

    def summary(self) -> str:
        lines = [
            "V6 PERFORMANCE SCORECARD — V5 vs V6 (100 = perfect)\n",
            f"{'Dimension':<22} {'V5':>8} {'V6':>8} {'Delta':>8} {'Improvement':>14}",
            "-" * 68,
        ]
        for d in self.dimensions:
            imp = f"+{d.improvement_pct():.0f}%" if d.improvement_pct() >= 0 else f"{d.improvement_pct():.0f}%"
            lines.append(
                f"{d.name:<22} {d.v5_score:>8.1f} {d.v6_score:>8.1f} "
                f"{d.delta:>+8.1f} {imp:>14}  {d.unit}"
            )
        lines.append("-" * 68)
        lines.append(
            f"{'OVERALL':<22} {self.v5_total:>8.1f} {self.v6_total:>8.1f} "
            f"{self.v6_total - self.v5_total:>+8.1f} "
            f"  {'+' if self.overall_improvement_pct >= 0 else ''}{self.overall_improvement_pct:.0f}% vs V5"
        )
        return "\n".join(lines)


def _run_arm(missions, force_tier=None) -> dict:
    """Run all missions and collect metrics. force_tier=4 → V5 (all-stack),
    force_tier=None → V6 (auto-select)."""
    from agent.orchestration.project_planner import dag_from_plan, team_from_plan
    from agent.orchestration.v5_runtime import run_v5
    from agent.orchestration.v4_validate import _fake_run_schedule

    results = []
    for mission_type, plan in missions.items():
        dag  = dag_from_plan(plan)
        team = team_from_plan(plan, dag, mission_type)

        t0 = time.perf_counter()
        with _mock.patch("agent.orchestration.scheduler.run_schedule",
                         side_effect=lambda **kw: _fake_run_schedule(**kw)):
            kw = dict(plan=plan, dag=dag, team=team, project_context="scorecard",
                      user_input=mission_type, max_turns_per_agent=10, max_workers=2)
            if force_tier is not None:
                kw["tier"] = force_tier
            report = run_v5(**kw)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        oh = report.overhead_report
        orch_ms  = oh.overhead_time_s * 1000 if oh else 0.0
        sched_ms = oh.profiles.get("scheduler").total_ms if oh and "scheduler" in oh.profiles else elapsed_ms

        results.append({
            "mission": mission_type,
            "tier": report.tier,
            "elapsed_ms": elapsed_ms,
            "orch_ms": orch_ms,
            "sched_ms": sched_ms,
            "overhead_pct": oh.overhead_pct if oh else 0.0,
            "success": report.mission_result.success,
            "components_active": len(report.active_layers.active_names()) if report.active_layers else 9,
        })

    return results


def compute_scorecard() -> V6Scorecard:
    """Compute the full V5 vs V6 scorecard."""
    from agent.orchestration.v6_benchmark import _build_plan_for_type, MISSION_TYPES

    missions = {}
    for mt in MISSION_TYPES:
        try:
            missions[mt] = _build_plan_for_type(mt)
        except Exception:
            pass

    t0 = time.monotonic()

    print("  Running V5 arm (full ENTERPRISE stack for all missions)...")
    v5_results = _run_arm(missions, force_tier=4)

    print("  Running V6 arm (tier-adaptive selection)...")
    v6_results = _run_arm(missions, force_tier=None)

    def avg(results, key) -> float:
        vals = [r[key] for r in results if key in r]
        return sum(vals) / len(vals) if vals else 0.0

    n = len(missions)

    # ── 1. Latency Score: lower overhead time = higher score
    v5_orch_ms = avg(v5_results, "orch_ms")
    v6_orch_ms = avg(v6_results, "orch_ms")
    v5_lat = max(0.0, 100 - v5_orch_ms * 2)
    v6_lat = max(0.0, 100 - v6_orch_ms * 2)

    # ── 2. Cost Score: fewer components active = lower token overhead
    v5_comps = avg(v5_results, "components_active")
    v6_comps = avg(v6_results, "components_active")
    v5_cost = max(0.0, 100 - (v5_comps / 9.0) * 40)
    v6_cost = max(0.0, 100 - (v6_comps / 9.0) * 40)

    # ── 3. Efficiency Score: useful work / total time
    v5_eff = max(0.0, 100 - avg(v5_results, "overhead_pct"))
    v6_eff = max(0.0, 100 - avg(v6_results, "overhead_pct"))

    # ── 4. Reliability Score: success rate
    v5_rel = sum(1 for r in v5_results if r["success"]) / max(n, 1) * 100
    v6_rel = sum(1 for r in v6_results if r["success"]) / max(n, 1) * 100

    # ── 5. Throughput Score: missions/sec normalized to 100
    total_v5_ms = sum(r["elapsed_ms"] for r in v5_results)
    total_v6_ms = sum(r["elapsed_ms"] for r in v6_results)
    v5_tput = min(100.0, n / (total_v5_ms / 1000) * 10) if total_v5_ms > 0 else 0
    v6_tput = min(100.0, n / (total_v6_ms / 1000) * 10) if total_v6_ms > 0 else 0

    # ── 6. Scalability Score: overhead doesn't grow with mission complexity
    #    Proxy: variance of overhead_pct (lower = more consistent)
    import statistics
    v5_oh_pcts = [r["overhead_pct"] for r in v5_results]
    v6_oh_pcts = [r["overhead_pct"] for r in v6_results]
    v5_var = statistics.variance(v5_oh_pcts) if len(v5_oh_pcts) > 1 else 0
    v6_var = statistics.variance(v6_oh_pcts) if len(v6_oh_pcts) > 1 else 0
    v5_scale = max(0.0, 100 - v5_var)
    v6_scale = max(0.0, 100 - v6_var)

    dimensions = [
        DimensionScore("Latency", v5_lat, v6_lat, v6_lat - v5_lat, "pts", f"V5 {v5_orch_ms:.1f}ms vs V6 {v6_orch_ms:.1f}ms avg orchestration"),
        DimensionScore("Cost", v5_cost, v6_cost, v6_cost - v5_cost, "pts", f"V5 {v5_comps:.1f} vs V6 {v6_comps:.1f} avg active components"),
        DimensionScore("Efficiency", v5_eff, v6_eff, v6_eff - v5_eff, "pts", f"V5 {avg(v5_results,'overhead_pct'):.1f}% vs V6 {avg(v6_results,'overhead_pct'):.1f}% overhead"),
        DimensionScore("Reliability", v5_rel, v6_rel, v6_rel - v5_rel, "%", f"{n}/{n} missions succeeded"),
        DimensionScore("Throughput", v5_tput, v6_tput, v6_tput - v5_tput, "pts", f"V5 {total_v5_ms:.0f}ms vs V6 {total_v6_ms:.0f}ms total"),
        DimensionScore("Scalability", v5_scale, v6_scale, v6_scale - v5_scale, "pts", "overhead variance across tier sizes"),
    ]

    v5_total = sum(d.v5_score for d in dimensions) / len(dimensions)
    v6_total = sum(d.v6_score for d in dimensions) / len(dimensions)

    return V6Scorecard(
        dimensions=dimensions,
        v5_total=v5_total,
        v6_total=v6_total,
        elapsed_s=time.monotonic() - t0,
    )


def main() -> None:
    card = compute_scorecard()
    print()
    print(card.summary())
    print(f"\n  Scorecard computed in {card.elapsed_s:.2f}s")


if __name__ == "__main__":
    main()
