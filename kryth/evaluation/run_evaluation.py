"""CLI entry point for the KRYTH autonomous evaluation framework.

Usage:
    python evaluation/run_evaluation.py --workspace /tmp/kryth_bm_M1_xxx
    python evaluation/run_evaluation.py --workspace /tmp/... --mission-id M1 --passed
    python evaluation/run_evaluation.py --eval-run evaluation_history/eval_xxx.json
    python evaluation/run_evaluation.py --compare eval_xxx.json eval_yyy.json
    python evaluation/run_evaluation.py --report report.md
    python evaluation/run_evaluation.py --list

After a benchmark run (with --keep-workspace):
    python evaluation/run_evaluation.py \\
        --benchmark-run benchmark_history/run_xxx.json \\
        --workspace-map M1:/tmp/kryth_bm_M1_xxx,M2:/tmp/kryth_bm_M2_yyy
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
_PROJECT_ROOT = str(_HERE.parent)
_KRYTH_SRC = str(_HERE.parent / "kryth" / "src")
for _p in (_PROJECT_ROOT, _KRYTH_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="KRYTH Autonomous Evaluation Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--workspace", "-w",
                   help="Evaluate a single workspace directory")
    g.add_argument("--eval-run",
                   help="Load and report an existing evaluation run JSON")
    g.add_argument("--benchmark-run",
                   help="Load a benchmark run JSON and evaluate all missions")
    g.add_argument("--list", "-l", action="store_true",
                   help="List saved evaluation runs")

    p.add_argument("--mission-id", default="",
                   help="Mission ID when using --workspace (e.g. M1)")
    p.add_argument("--mission-name", default="",
                   help="Mission name when using --workspace")
    p.add_argument("--passed", action="store_true",
                   help="Mark mission as passed (for --workspace mode)")
    p.add_argument("--workspace-map", default="",
                   help="Comma-separated ID:path pairs for --benchmark-run "
                        "(e.g. M1:/tmp/ws1,M2:/tmp/ws2)")
    p.add_argument("--compare", "-c", nargs=2, metavar=("OLD", "NEW"),
                   help="Compare two evaluation run JSON files")
    p.add_argument("--report", "-r", default="",
                   help="Write Markdown report to this path")
    p.add_argument("--history-dir", default="",
                   help="Directory for evaluation history")
    p.add_argument("--no-llm", action="store_true",
                   help="Use only static analysis (no LLM reviewer calls)")
    p.add_argument("--no-dashboard", action="store_true",
                   help="Disable live terminal dashboard")
    p.add_argument("--reviewer-timeout", type=float, default=60.0,
                   help="Per-reviewer LLM timeout in seconds (default: 60)")
    return p.parse_args()


def _get_kryth_version() -> str:
    try:
        v = (_HERE.parent / "kryth" / "VERSION").read_text().strip()
        return v
    except Exception:
        return "unknown"


def main() -> int:
    args = _parse_args()

    from evaluation.evaluation_metrics import EvaluationRun
    from evaluation.evaluation_runner import evaluate_mission
    from evaluation.evaluation_storage import (
        save_evaluation_run, load_evaluation_run,
        list_evaluation_runs, DEFAULT_EVAL_HISTORY_DIR,
    )
    from evaluation.evaluation_compare import compare_evaluation_runs
    from evaluation.evaluation_report import (
        generate_evaluation_markdown,
        generate_evaluation_text_summary,
    )
    from evaluation.evaluation_dashboard import EvaluationDashboard

    history_dir = args.history_dir or DEFAULT_EVAL_HISTORY_DIR

    # ── --list ─────────────────────────────────────────────────────────────────
    if args.list:
        runs = list_evaluation_runs(history_dir)
        if not runs:
            print("No evaluation runs found.")
        for r in runs:
            print(f"  {Path(r).name}")
        return 0

    # ── --compare ──────────────────────────────────────────────────────────────
    if args.compare:
        old_run = load_evaluation_run(args.compare[0])
        new_run = load_evaluation_run(args.compare[1])
        cmp = compare_evaluation_runs(old_run, new_run)
        for line in cmp.summary_lines():
            print(line)
        if args.report:
            generate_evaluation_markdown(new_run, comparison=cmp, output_path=args.report)
            print(f"Report written: {args.report}")
        return 1 if cmp.has_regressions else 0

    # ── --eval-run ─────────────────────────────────────────────────────────────
    if args.eval_run:
        run = load_evaluation_run(args.eval_run)
        print(generate_evaluation_text_summary(run))
        if args.report:
            generate_evaluation_markdown(run, output_path=args.report)
            print(f"Report written: {args.report}")
        return 0

    # ── --workspace (single mission) ───────────────────────────────────────────
    if args.workspace:
        ws = args.workspace
        if not Path(ws).exists():
            print(f"Workspace not found: {ws}", file=sys.stderr)
            return 1

        result = evaluate_mission(
            workspace=ws,
            mission_id=args.mission_id or "EVAL",
            mission_name=args.mission_name or Path(ws).name,
            mission_passed=args.passed,
            use_llm=not args.no_llm,
            reviewer_timeout_s=args.reviewer_timeout,
        )
        print(f"\nEvaluation complete: {result.mission_id} — {result.mission_name}")
        print(f"Overall score: {result.scores.overall}/100  Grade: {result.scores.grade()}")
        print()
        for dim in ("correctness", "code_quality", "architecture", "testing",
                    "performance", "security", "maintainability", "documentation"):
            val = getattr(result.scores, dim)
            bar = "█" * (val // 10) + "░" * (10 - val // 10)
            print(f"  {dim:<22}  [{bar}] {val:3d}")

        if args.report:
            run = EvaluationRun(kryth_version=_get_kryth_version())
            run.results.append(result)
            generate_evaluation_markdown(run, output_path=args.report)
            print(f"\nReport written: {args.report}")
        return 0

    # ── --benchmark-run ────────────────────────────────────────────────────────
    if args.benchmark_run:
        import json as _json
        with open(args.benchmark_run, encoding="utf-8") as fh:
            bm_dict = _json.load(fh)

        # Parse workspace map
        ws_map: dict[str, str] = {}
        if args.workspace_map:
            for pair in args.workspace_map.split(","):
                if ":" in pair:
                    mid, path = pair.split(":", 1)
                    ws_map[mid.strip()] = path.strip()

        missions = bm_dict.get("missions", [])
        mission_ids = [m.get("mission_id", "") for m in missions]

        dash: EvaluationDashboard | None = None
        if not args.no_dashboard and sys.stderr.isatty():
            dash = EvaluationDashboard(mission_ids=mission_ids)
            dash.start()

        run = EvaluationRun(
            benchmark_run_id=bm_dict.get("run_id", ""),
            kryth_version=bm_dict.get("kryth_version", _get_kryth_version()),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        for m_dict in missions:
            mid = m_dict.get("mission_id", "")
            ws = ws_map.get(mid)
            if not ws:
                print(f"  [{mid}] no workspace path — skipping", flush=True)
                continue
            if not Path(ws).exists():
                print(f"  [{mid}] workspace not found: {ws} — skipping", flush=True)
                continue

            if dash:
                dash.mark_evaluating(mid)

            result = evaluate_mission(
                workspace=ws,
                mission_id=mid,
                mission_name=m_dict.get("mission_name", ""),
                run_id=bm_dict.get("run_id", ""),
                mission_passed=m_dict.get("success", False),
                benchmark_metrics=m_dict,
                use_llm=not args.no_llm,
                reviewer_timeout_s=args.reviewer_timeout,
            )
            run.results.append(result)

            if dash:
                # Feed reviewer scores into dashboard
                for dim, rs in result.review_scores.items():
                    dash.update_reviewer(mid, dim, rs)
                dash.mark_done(mid, result)

        if dash:
            dash.stop()

        print(generate_evaluation_text_summary(run))

        saved = save_evaluation_run(run, history_dir=history_dir)
        print(f"Evaluation saved: {saved}")

        report_path = args.report or saved.replace(".json", ".md")
        generate_evaluation_markdown(run, output_path=report_path)
        print(f"Report written: {report_path}")

        return 0

    # No mode selected
    print("Usage: python evaluation/run_evaluation.py --help")
    return 1


if __name__ == "__main__":
    sys.exit(main())
