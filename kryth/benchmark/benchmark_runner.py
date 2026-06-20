"""Runs individual benchmark missions with isolation, timeout, and full metrics."""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Optional

from .benchmark_metrics import (
    MissionMetrics,
    MissionTimings,
    MetricsCollector,
)
from .benchmark_tasks import MissionDef


# Directory where KRYTH source lives (relative to this file: ../kryth/src)
_KRYTH_SRC = str(
    (Path(__file__).parent.parent / "kryth" / "src").resolve()
)


def _add_kryth_to_path() -> None:
    if _KRYTH_SRC not in sys.path:
        sys.path.insert(0, _KRYTH_SRC)


# ── Workspace management ──────────────────────────────────────────────────────

def _make_workspace(mission: MissionDef, base_dir: Optional[str] = None) -> str:
    """Create a clean temp directory and call mission.setup()."""
    parent = base_dir or tempfile.gettempdir()
    ws = tempfile.mkdtemp(prefix=f"kryth_bm_{mission.id}_", dir=parent)
    try:
        mission.setup(ws)
    except Exception as exc:
        print(f"  [runner] setup failed for {mission.id}: {exc}", flush=True)
    return ws


def _cleanup_workspace(ws: str) -> None:
    try:
        shutil.rmtree(ws, ignore_errors=True)
    except Exception:
        pass


# ── Agent runner ──────────────────────────────────────────────────────────────

class _AgentThread(threading.Thread):
    """Runs run_agent() in a thread so we can enforce a hard timeout."""

    def __init__(self, prompt: str, workspace: str, collector: MetricsCollector):
        super().__init__(daemon=True)
        self.prompt = prompt
        self.workspace = workspace
        self.collector = collector
        self.exception: Optional[Exception] = None
        self._orig_cwd: Optional[str] = None

    def run(self) -> None:
        self._orig_cwd = os.getcwd()
        try:
            _add_kryth_to_path()
            from agent.agent_loop import run_agent
            from agent.session import reset_session, get_session

            # Clean session with yolo profile
            reset_session()
            s = get_session()
            s.profile = "yolo"
            os.environ["KRYTH_PROFILE"] = "yolo"
            os.environ["KRYTH_NO_BROWSER"] = "1"

            # Complexity-based multi-agent mode selection:
            #   simple/medium/single-component → ALWAYS_SINGLE (fast, predictable)
            #   true full-stack (has both frontend AND backend keywords) → SESSION_APPROVED
            import re
            _FRONTEND_RE = re.compile(
                r'\b(frontend|react|vue|angular|next\.?js|nuxt|svelte|'
                r'html\s+page|index\.html|ui\s+component|web\s+app)\b', re.I)
            _BACKEND_RE = re.compile(
                r'\b(backend|fastapi|express|flask|django|spring|rails|'
                r'rest\s+api|graphql\s+api|api\s+server)\b', re.I)
            _is_fullstack = bool(
                _FRONTEND_RE.search(self.prompt) and _BACKEND_RE.search(self.prompt)
            )
            try:
                from agent.task_classifier import classify_task
                tp = classify_task(self.prompt)
                if tp.complexity == "complex" and tp.has_independent_subtasks and _is_fullstack:
                    s.multi_agent_mode = "SESSION_APPROVED"
                    print(f"  Task: {tp.complexity} / {tp.category} [full-stack] — {tp.reason}")
                else:
                    s.multi_agent_mode = "ALWAYS_SINGLE"
                    print(f"  Task: {tp.complexity} / {tp.category} — {tp.reason}")
            except Exception:
                s.multi_agent_mode = "ALWAYS_SINGLE"

            os.chdir(self.workspace)
            with self.collector:
                _result = run_agent(self.prompt)
                if _result and getattr(_result, 'status', None) not in ('done', None):
                    print(f"  [runner] agent status={_result.status} turns={getattr(_result,'turns_used',0)}", flush=True)
            # Capture turn count
            self.collector._m.turns_used = len([
                m for m in s.messages if m.get("role") == "assistant"
            ])
        except Exception as exc:
            self.exception = exc
        finally:
            if self._orig_cwd:
                try:
                    os.chdir(self._orig_cwd)
                except Exception:
                    pass


# ── Core public API ───────────────────────────────────────────────────────────

def run_mission(
    mission: MissionDef,
    timeout_s: int = 300,
    workspace_base: Optional[str] = None,
    keep_workspace: bool = False,
    verbose: bool = False,
) -> MissionMetrics:
    """Execute a single mission end-to-end and return complete metrics.

    Args:
        mission: The mission definition to run.
        timeout_s: Hard wall-clock timeout in seconds (thread is abandoned,
                   not killed — Python cannot force-kill threads).
        workspace_base: Parent dir for temp workspace; defaults to system tmp.
        keep_workspace: If True, the workspace dir is not deleted after the run.
        verbose: Print progress lines to stdout.

    Returns:
        Fully populated MissionMetrics.
    """
    metrics = MissionMetrics(
        mission_id=mission.id,
        mission_name=mission.name,
        prompt=mission.prompt,
    )

    ws = _make_workspace(mission, workspace_base)
    metrics.workspace = ws

    if verbose:
        print(f"\n{'─'*60}", flush=True)
        print(f"  [{mission.id}] {mission.name}", flush=True)
        print(f"  workspace : {ws}", flush=True)
        print(f"  timeout   : {timeout_s}s", flush=True)

    collector = MetricsCollector(metrics)
    thread = _AgentThread(mission.prompt, ws, collector)

    wall_start = time.monotonic()
    thread.start()
    thread.join(timeout=timeout_s)
    wall_elapsed = time.monotonic() - wall_start

    if thread.is_alive():
        # Thread still running — timeout hit; still run the check on whatever was written
        try:
            collector.__exit__(None, None, None)
        except Exception:
            pass
        try:
            metrics.success = bool(mission.check(ws))
        except Exception:
            metrics.success = False
        metrics.error = f"TIMEOUT after {timeout_s}s"
        if verbose:
            status = "PASS" if metrics.success else "TIMEOUT"
            print(f"  [{mission.id}] {status} after {timeout_s}s", flush=True)
    elif thread.exception is not None:
        tb = traceback.format_exception(
            type(thread.exception), thread.exception, thread.exception.__traceback__
        )
        metrics.error = "".join(tb)[-2000:]  # cap at 2k chars
        metrics.success = False
        if verbose:
            print(f"  [{mission.id}] EXCEPTION: {thread.exception}", flush=True)
    else:
        # Run success check
        try:
            metrics.success = bool(mission.check(ws))
        except Exception as exc:
            metrics.success = False
            metrics.error = f"check() raised: {exc}"
        if verbose:
            status = "PASS" if metrics.success else "FAIL"
            print(f"  [{mission.id}] {status} in {wall_elapsed:.1f}s", flush=True)

    # Count files written (truth from filesystem, not just events)
    if not metrics.files_written:
        try:
            all_files = list(Path(ws).rglob("*.*"))
            setup_files = set()
            try:
                # Re-setup in a temp dir to get baseline count
                tmp = tempfile.mkdtemp()
                mission.setup(tmp)
                setup_files = {f.name for f in Path(tmp).rglob("*.*")}
                shutil.rmtree(tmp, ignore_errors=True)
            except Exception:
                pass
            new_files = [f for f in all_files if f.name not in setup_files]
            metrics.files_written = max(metrics.files_written, len(new_files))
        except Exception:
            pass

    if not keep_workspace:
        _cleanup_workspace(ws)
        metrics.workspace = "[cleaned]"

    return metrics


def run_missions_parallel(
    missions: list[MissionDef],
    timeout_s: int = 300,
    max_parallel: int = 2,
    workspace_base: Optional[str] = None,
    verbose: bool = True,
) -> list[MissionMetrics]:
    """Run multiple missions with bounded parallelism.

    Missions run at most `max_parallel` at a time using a thread pool.
    Each mission gets its own workspace so there is no interference.
    """
    import concurrent.futures

    results: list[Optional[MissionMetrics]] = [None] * len(missions)

    def _run(idx: int, m: MissionDef) -> tuple[int, MissionMetrics]:
        r = run_mission(
            m,
            timeout_s=timeout_s,
            workspace_base=workspace_base,
            verbose=verbose,
        )
        return idx, r

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as ex:
        futures = {
            ex.submit(_run, i, m): i for i, m in enumerate(missions)
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                idx, r = future.result()
                results[idx] = r
            except Exception as exc:
                idx = futures[future]
                m = missions[idx]
                r = MissionMetrics(
                    mission_id=m.id,
                    mission_name=m.name,
                    error=str(exc),
                    success=False,
                )
                results[idx] = r

    return [r for r in results if r is not None]
