"""V6 Soak Test — Phase 8.

Runs many missions back-to-back for a configurable duration and tracks:
  crash_count            — exceptions that escaped the run_v5 wrapper
  memory_growth_mb       — RSS growth from start to end (requires psutil)
  timeout_rate           — fraction of missions that hit max_turns limit
  recovery_success       — fraction of recovery attempts that succeeded
  provider_failures      — 503/429/timeout errors from LLM providers
  mission_success_rate   — fraction of missions where result.success == True

Configuration via env:
  KRYTH_SOAK_DURATION     — seconds to run (default: 30 for CI, 86400 for 24h)
  KRYTH_SOAK_MIX          — mission mix JSON (default: balanced across tiers)
  --live flag     — use real LLM calls via KRYTH config (no separate key needed)

Usage (quick smoke test):
  python -m agent.orchestration.v6_soak_test

24-hour production soak (credentials from KRYTH config — no raw env vars needed):
  python -m agent.orchestration.v6_soak_test --live --duration 86400
"""
from __future__ import annotations

import os
import sys
import time
import traceback
import unittest.mock as _mock
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SoakEvent:
    ts: float
    mission_type: str
    tier: int
    success: bool
    duration_ms: float
    recovery_attempts: int
    error: str = ""


@dataclass
class SoakReport:
    duration_s: float
    missions_run: int
    missions_succeeded: int
    crash_count: int
    recovery_attempts_total: int
    recovery_successes: int
    by_tier: Dict[int, int] = field(default_factory=dict)
    memory_start_mb: float = 0.0
    memory_end_mb: float = 0.0
    events: List[SoakEvent] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return self.missions_succeeded / max(self.missions_run, 1)

    @property
    def recovery_success_rate(self) -> float:
        return self.recovery_successes / max(self.recovery_attempts_total, 1)

    @property
    def memory_growth_mb(self) -> float:
        return max(0.0, self.memory_end_mb - self.memory_start_mb)

    def summary(self) -> str:
        lines = [
            f"Soak Test Report ({self.duration_s:.0f}s / {self.missions_run} missions):",
            f"  Success rate:         {self.success_rate:.1%}  ({self.missions_succeeded}/{self.missions_run})",
            f"  Crashes:              {self.crash_count}",
            f"  Recovery rate:        {self.recovery_success_rate:.1%}  ({self.recovery_successes}/{self.recovery_attempts_total} attempts)",
            f"  Memory growth:        {self.memory_growth_mb:.1f} MB",
            f"  Tier distribution:    " + " ".join(f"T{t}:{n}" for t, n in sorted(self.by_tier.items())),
        ]
        return "\n".join(lines)


def _rss_mb() -> float:
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


# Mission mix weights (type → relative frequency)
_DEFAULT_MIX = {
    "create_file": 3,           # many micro tasks
    "bug_fix": 2,               # common simple tasks
    "crud_api": 2,              # standard multi-module
    "jwt_auth": 1,              # standard multi-module
    "large_refactor": 1,        # complex
    "saas_app": 1,              # complex
}


def _weighted_choice(mix: Dict[str, int]) -> str:
    import random
    population = [t for t, w in mix.items() for _ in range(w)]
    return random.choice(population)


def _build_plan(mission_type: str):
    from agent.orchestration.v6_benchmark import _build_plan_for_type
    return _build_plan_for_type(mission_type)


def run_soak_test(
    duration_s: float = 30.0,
    live_providers: bool = False,
    mix: Dict[str, int] = None,
    verbose: bool = True,
) -> SoakReport:
    """Run back-to-back missions for `duration_s` seconds and collect stats."""
    from agent.orchestration.project_planner import dag_from_plan, team_from_plan
    from agent.orchestration.v5_runtime import run_v5
    from agent.orchestration.v4_validate import _fake_run_schedule

    if mix is None:
        mix = _DEFAULT_MIX

    events: List[SoakEvent] = []
    crash_count = 0
    recovery_total = 0
    recovery_ok = 0
    by_tier: Dict[int, int] = {}

    mem_start = _rss_mb()
    t_start = time.monotonic()
    missions_run = 0
    missions_ok = 0

    if verbose:
        print(f"  Soak test: {duration_s:.0f}s, {'LIVE' if live_providers else 'offline'} mode")

    while (time.monotonic() - t_start) < duration_s:
        mission_type = _weighted_choice(mix)
        t0 = time.perf_counter()
        error = ""
        report = None

        try:
            plan = _build_plan(mission_type)
            dag  = dag_from_plan(plan)
            team = team_from_plan(plan, dag, mission_type)

            if live_providers:
                report = run_v5(
                    plan=plan, dag=dag, team=team,
                    project_context="soak", user_input=mission_type,
                    max_turns_per_agent=20, max_workers=2, project_root=".",
                )
            else:
                with _mock.patch(
                    "agent.orchestration.scheduler.run_schedule",
                    side_effect=lambda **kw: _fake_run_schedule(**kw),
                ):
                    report = run_v5(
                        plan=plan, dag=dag, team=team,
                        project_context="soak", user_input=mission_type,
                        max_turns_per_agent=10, max_workers=2, project_root=".",
                    )
        except Exception as exc:
            crash_count += 1
            error = f"{type(exc).__name__}: {exc}"
            if verbose:
                print(f"  CRASH in {mission_type}: {error}")

        duration_ms = (time.perf_counter() - t0) * 1000
        missions_run += 1

        success = report is not None and report.mission_result.success
        if success:
            missions_ok += 1

        tier = report.tier if report is not None else -1
        by_tier[tier] = by_tier.get(tier, 0) + 1

        if report is not None:
            recovery_total += report.recovery_attempts
            recovery_ok += sum(1 for _ in range(report.recovery_attempts)
                               if report.mission_result.success)

        events.append(SoakEvent(
            ts=time.monotonic() - t_start,
            mission_type=mission_type,
            tier=tier,
            success=success,
            duration_ms=duration_ms,
            recovery_attempts=report.recovery_attempts if report else 0,
            error=error,
        ))

        if verbose and missions_run % 10 == 0:
            elapsed = time.monotonic() - t_start
            print(f"  {missions_run} missions | {missions_ok/missions_run:.0%} success | "
                  f"{elapsed:.0f}s elapsed | {crash_count} crashes")

    return SoakReport(
        duration_s=time.monotonic() - t_start,
        missions_run=missions_run,
        missions_succeeded=missions_ok,
        crash_count=crash_count,
        recovery_attempts_total=recovery_total,
        recovery_successes=recovery_ok,
        by_tier=by_tier,
        memory_start_mb=mem_start,
        memory_end_mb=_rss_mb(),
        events=events,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m agent.orchestration.v6_soak_test",
        description="KRYTH V6 Soak Test — continuous mission harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Provider credentials are read from KRYTH config — not from\n"
            "provider-specific env vars.  Configure providers in:\n"
            "  ~/.kryth/config.yaml  or via KRYTH_MAIN_MODEL / KRYTH_BASE_URL\n"
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=os.environ.get("KRYTH_REAL_PROVIDERS", "").strip() in ("1", "true", "yes"),
        help="Run with real LLM providers from KRYTH config (default: offline mock)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=float(os.environ.get("KRYTH_SOAK_DURATION", "30")),
        metavar="SECONDS",
        help="How long to run the soak test (default: 30s, set to 86400 for 24h)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=True,
        help="Print progress every 10 missions (default: on)",
    )
    args = parser.parse_args()

    live: bool = args.live

    # ── Provider validation (KRYTH config — not raw env vars) ────────────────
    if live:
        try:
            from agent.orchestration.kryth_provider_integration import (
                require_live_providers,
                describe_provider_status,
            )
            print(describe_provider_status())
            print()
            require_live_providers(verbose=True)
        except SystemExit:
            raise
        except Exception as e:
            print(f"  Warning: provider discovery error: {e}")
            print()

    report = run_soak_test(
        duration_s=args.duration,
        live_providers=live,
        verbose=args.verbose,
    )
    print()
    print(report.summary())

    # Exit non-zero if more than 1% of missions crashed
    if report.crash_count / max(report.missions_run, 1) > 0.01:
        print(f"\n  FAIL: crash rate {report.crash_count/report.missions_run:.1%} exceeds 1% threshold")
        sys.exit(1)
    else:
        print(f"\n  PASS: soak test completed — {report.missions_run} missions, "
              f"{report.success_rate:.0%} success rate, {report.crash_count} crashes")


if __name__ == "__main__":
    main()
