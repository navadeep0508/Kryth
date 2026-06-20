"""CLI entry point for the KRYTH benchmark suite.

Usage:
    python benchmark/run_benchmark.py
    python benchmark/run_benchmark.py --missions M1,M2,M3
    python benchmark/run_benchmark.py --timeout 300 --parallel 2
    python benchmark/run_benchmark.py --compare benchmark_history/run_20260612_123000_abc123.json
    python benchmark/run_benchmark.py --report report.md
    python benchmark/run_benchmark.py --list
    python benchmark/run_benchmark.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

# Add project root (parent of benchmark/) and kryth/src to import path
_HERE = Path(__file__).parent.resolve()
_PROJECT_ROOT = str(_HERE.parent)
_KRYTH_SRC = str(_HERE.parent / "kryth" / "src")
for _p in (_PROJECT_ROOT, _KRYTH_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="KRYTH Autonomous Benchmark Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--missions", "-m",
        help="Comma-separated mission IDs to run (e.g. M1,M2). Default: all.",
        default="",
    )
    p.add_argument(
        "--timeout", "-t",
        type=int,
        default=int(os.environ.get("KRYTH_MISSION_TIMEOUT", "600")),
        help="Per-mission timeout in seconds (default: 600)",
    )
    p.add_argument(
        "--parallel", "-p",
        type=int,
        default=1,
        help="Max missions to run in parallel (default: 1 — sequential)",
    )
    p.add_argument(
        "--compare", "-c",
        help="Path to a previous run JSON to compare against",
        default="",
    )
    p.add_argument(
        "--report", "-r",
        help="Write Markdown report to this path",
        default="",
    )
    p.add_argument(
        "--history-dir",
        help="Directory for run history JSON files",
        default="",
    )
    p.add_argument(
        "--keep-workspace",
        action="store_true",
        help="Do not delete mission workspaces after each run",
    )
    p.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Disable live terminal dashboard",
    )
    p.add_argument(
        "--list", "-l",
        action="store_true",
        help="List available missions and exit",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would run without actually running",
    )
    p.add_argument(
        "--version",
        action="store_true",
        help="Print KRYTH version and exit",
    )
    return p.parse_args()


def _get_kryth_version() -> str:
    try:
        from agent import __version__
        return __version__
    except Exception:
        pass
    try:
        v_file = _HERE.parent / "kryth" / "VERSION"
        if v_file.exists():
            return v_file.read_text().strip()
    except Exception:
        pass
    return "unknown"


def main() -> int:
    args = _parse_args()

    from benchmark.benchmark_tasks import ALL_MISSIONS, MISSION_BY_ID
    from benchmark.benchmark_metrics import BenchmarkRun, MissionMetrics
    from benchmark.benchmark_storage import (
        save_run, load_run, DEFAULT_HISTORY_DIR,
    )
    from benchmark.benchmark_compare import compare_runs
    from benchmark.benchmark_report import (
        generate_markdown_report,
        generate_text_summary,
        generate_profiling_report,
    )
    from benchmark.benchmark_dashboard import BenchmarkDashboard
    from benchmark.benchmark_runner import run_mission

    # ── --version ─────────────────────────────────────────────────────────────
    if args.version:
        print(f"KRYTH {_get_kryth_version()}")
        return 0

    # ── --list ─────────────────────────────────────────────────────────────────
    if args.list:
        print("Available missions:")
        for m in ALL_MISSIONS:
            print(f"  {m.id:4s}  [{m.category:8s}]  {m.name}")
        return 0

    # ── Select missions ────────────────────────────────────────────────────────
    if args.missions:
        selected_ids = [x.strip().upper() for x in args.missions.split(",")]
        unknown = [mid for mid in selected_ids if mid not in MISSION_BY_ID]
        if unknown:
            print(f"Unknown mission IDs: {', '.join(unknown)}", file=sys.stderr)
            return 1
        missions = [MISSION_BY_ID[mid] for mid in selected_ids]
    else:
        missions = list(ALL_MISSIONS)

    # ── --dry-run ──────────────────────────────────────────────────────────────
    if args.dry_run:
        print(f"Would run {len(missions)} missions (timeout={args.timeout}s, "
              f"parallel={args.parallel}):")
        for m in missions:
            print(f"  {m.id:4s}  {m.name}")
        return 0

    # ── Setup ──────────────────────────────────────────────────────────────────
    history_dir = args.history_dir or DEFAULT_HISTORY_DIR
    version = _get_kryth_version()

    run = BenchmarkRun(
        kryth_version=version,
        timeout_s=args.timeout,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    print(f"\nKRYTH Benchmark Suite  v{version}")
    print(f"Missions : {len(missions)}")
    print(f"Timeout  : {args.timeout}s each")
    print(f"Parallel : {args.parallel}")
    print(f"History  : {history_dir}")
    print()

    # ── Dashboard ──────────────────────────────────────────────────────────────
    mission_ids = [m.id for m in missions]
    dash: BenchmarkDashboard | None = None
    if not args.no_dashboard and sys.stderr.isatty():
        dash = BenchmarkDashboard(mission_ids=mission_ids)
        dash.start()

    # ── Run ────────────────────────────────────────────────────────────────────
    _inter_mission_delay = int(os.environ.get("KRYTH_INTER_MISSION_DELAY", "15"))
    if args.parallel <= 1:
        # Sequential
        for i, m in enumerate(missions):
            if i > 0 and _inter_mission_delay > 0:
                import time as _t
                _t.sleep(_inter_mission_delay)
            if dash:
                dash.mark_running(m.id)
            metrics = run_mission(
                m,
                timeout_s=args.timeout,
                keep_workspace=args.keep_workspace,
                verbose=(not dash),
            )
            run.missions.append(metrics)
            if dash:
                dash.mark_done(m.id, metrics)
    else:
        # Parallel with dashboard updates
        import concurrent.futures
        results: list[MissionMetrics | None] = [None] * len(missions)
        lock = threading.Lock()

        def _run_one(idx: int, m):
            if dash:
                dash.mark_running(m.id)
            metrics = run_mission(
                m,
                timeout_s=args.timeout,
                keep_workspace=args.keep_workspace,
                verbose=False,
            )
            with lock:
                results[idx] = metrics
            if dash:
                dash.mark_done(m.id, metrics)
            return idx, metrics

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as ex:
            futures = [ex.submit(_run_one, i, m) for i, m in enumerate(missions)]
            concurrent.futures.wait(futures)

        for r in results:
            if r is not None:
                run.missions.append(r)

    # ── Stop dashboard ─────────────────────────────────────────────────────────
    if dash:
        dash.stop()

    # ── Print text summary ─────────────────────────────────────────────────────
    print(generate_text_summary(run))

    # ── Save run ───────────────────────────────────────────────────────────────
    saved_path = save_run(run, history_dir=history_dir)
    print(f"Run saved: {saved_path}")

    # ── Compare ────────────────────────────────────────────────────────────────
    comparison = None
    if args.compare:
        try:
            old_run = load_run(args.compare)
            comparison = compare_runs(old_run, run)
            print()
            for line in comparison.summary_lines():
                print(line)
        except Exception as exc:
            print(f"[compare] failed to load {args.compare}: {exc}", file=sys.stderr)

    # ── Markdown report ────────────────────────────────────────────────────────
    if args.report:
        report_text = generate_markdown_report(
            run, comparison=comparison, output_path=args.report
        )
        print(f"Report written: {args.report}")
    else:
        # Always write a report next to the saved run
        report_path = saved_path.replace(".json", ".md")
        generate_markdown_report(run, comparison=comparison, output_path=report_path)
        print(f"Report written: {report_path}")

    # ── Profiling report (auto-generated after every run) ─────────────────────
    profile_path = saved_path.replace(".json", "_profile.md")
    try:
        profile_text = generate_profiling_report(run)
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(profile_text)
        print(f"Profiling report: {profile_path}")
    except Exception as exc:
        print(f"[profiling] failed: {exc}", file=sys.stderr)

    # ── Autonomous Engineering Report (self-optimization engine) ──────────────
    opt_path = saved_path.replace(".json", "_optimization.md")
    try:
        from optimization import optimize_run as _optimize_run
        # Load previous run for version comparison
        _prev_run = None
        try:
            from benchmark.benchmark_storage import list_runs as _list_runs, load_run as _load_run
            _all_runs = _list_runs(history_dir)
            # Previous = second-to-last (last is the one just saved)
            if len(_all_runs) >= 2:
                _prev_run = _load_run(_all_runs[-2])
        except Exception:
            pass
        _opt_result = _optimize_run(run, previous_run=_prev_run, output_path=opt_path)
        print(f"Optimization report: {opt_path}")
        # Print top 3 recommendations inline
        top_recs = _opt_result.recommendations.sorted()[:3]
        if top_recs:
            print(f"  Top recommendations:")
            for _r in top_recs:
                print(f"  [{_r.priority}] {_r.text}")
    except Exception as exc:
        print(f"[optimization] failed: {exc}", file=sys.stderr)

    # ── Exit code: 1 if any mission failed ────────────────────────────────────
    return 0 if run.passed == run.total else 1


if __name__ == "__main__":
    sys.exit(main())
