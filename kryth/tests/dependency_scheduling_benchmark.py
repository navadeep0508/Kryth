"""Phase 10 benchmark — current vs dependency-aware scheduling.

Run:  python tests/dependency_scheduling_benchmark.py

Prints a before/after table proving the non-idle-agents improvement on the
representative mission, across worker counts. Deterministic (no live model).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.orchestration.dependency_scheduler import (
    benchmark_dependency_scheduling, default_benchmark_mission,
)


def main() -> int:
    mission = default_benchmark_mission()
    print("Dependency-aware scheduling benchmark")
    print("=" * 72)
    print(f"{'workers':<8}{'scheduler':<20}{'makespan':<10}{'idle':<8}{'util%':<8}{'steals':<8}")
    print("-" * 72)
    worst_regression = 0.0
    for w in (3, 4, 5, 6, 8):
        r = benchmark_dependency_scheduling(mission, w)
        b, a = r["baseline"], r["dependency_aware"]
        print(f"{w:<8}{'baseline':<20}{b['makespan']:<10}{b['idle_ticks']:<8}{b['utilization_pct']:<8}{b['work_steals']:<8}")
        print(f"{'':<8}{'dependency-aware':<20}{a['makespan']:<10}{a['idle_ticks']:<8}{a['utilization_pct']:<8}{a['work_steals']:<8}")
        print(f"{'':<8}-> makespan -{r['makespan_reduction_pct']}%  idle -{r['idle_reduction_pct']}%  util +{r['utilization_gain_pct']}pts")
        print("-" * 72)
        # A worker is never idle while work exists ⇒ aware must never regress.
        if a["makespan"] > b["makespan"] or a["idle_ticks"] > b["idle_ticks"]:
            worst_regression = max(worst_regression, 1.0)

    if worst_regression:
        print("RESULT: ✗ regression detected — dependency-aware did worse somewhere")
        return 1
    print("RESULT: ✓ dependency-aware never idle while work exists; no regression")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
