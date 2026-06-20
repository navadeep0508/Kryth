"""Action Scheduler — executes an ActionGraph using existing KRYTH worker pools.

Parallel execution:
  - Layers are computed topologically; all actions in one layer run in parallel
  - Uses ThreadPoolExecutor (same pattern as orchestration/scheduler.py)
  - Respects ownership locks on affected_paths (via orchestration.ownership)
  - Respects rollback boundaries: a failed action halts its dependent layer

Flow per action:
  1. RollbackEngine.snapshot()
  2. ExecutorSelector.select()
  3. executor.execute()
  4. ActionVerifier.verify()
  5. ActionMemory.record()
  6. ActionAnalytics.record()
  7. On failure: RollbackEngine.rollback()
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from agent.action.action import Action, ActionResult, ActionStatus
from agent.action.action_graph import ActionGraph
from agent.action.action_memory import ActionMemory
from agent.action.action_registry import ActionRegistry, get_registry
from agent.action.action_verifier import ActionVerifier
from agent.action.action_analytics import ActionAnalytics, ActionMetric
from agent.action.executor_selector import ExecutorSelector
from agent.action.rollback_engine import RollbackEngine

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[Action, str], None]   # (action, status)


@dataclass
class SchedulerResult:
    success: bool
    mission: str = ""
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    rolled_back: int = 0
    elapsed_ms: float = 0.0
    outputs: dict[str, ActionResult] = field(default_factory=dict)
    analytics: dict = field(default_factory=dict)


class ActionScheduler:
    """Executes an ActionGraph — the UAL kernel scheduler."""

    def __init__(
        self,
        registry: Optional[ActionRegistry] = None,
        memory: Optional[ActionMemory] = None,
        analytics: Optional[ActionAnalytics] = None,
        max_workers: int = 8,
        on_progress: Optional[ProgressCallback] = None,
    ) -> None:
        self._registry  = registry or get_registry()
        self._memory    = memory or ActionMemory()
        self._analytics = analytics or ActionAnalytics()
        self._selector  = ExecutorSelector(self._registry, self._memory)
        self._verifier  = ActionVerifier()
        self._rollback  = RollbackEngine()
        self._max_workers = max_workers
        self._on_progress = on_progress

    # ── Public API ────────────────────────────────────────────────────

    def run(self, graph: ActionGraph) -> SchedulerResult:
        """Execute the ActionGraph layer by layer with parallelism within layers."""
        errors = graph.validate()
        if errors:
            return SchedulerResult(
                success=False, mission=graph.mission,
                analytics={"errors": errors},
            )

        start = time.time()
        outputs: dict[str, ActionResult] = {}
        failed_ids: set[str] = set()

        for layer_idx, layer in enumerate(graph.layers()):
            logger.info(
                "Layer %d: %d action(s) — %s",
                layer_idx,
                len(layer),
                [a.id for a in layer],
            )

            # Skip actions whose dependencies failed
            runnable = [a for a in layer if not (set(a.dependencies) & failed_ids)]
            skipped  = [a for a in layer if     (set(a.dependencies) & failed_ids)]
            for a in skipped:
                a.status = ActionStatus.SKIPPED
                logger.warning("Skipping '%s' — dependency failed", a.id)

            if not runnable:
                continue

            layer_results = self._run_layer(runnable)
            for action, result in layer_results:
                outputs[action.id] = result
                if not result.success:
                    failed_ids.add(action.id)

        elapsed_ms = (time.time() - start) * 1000
        all_actions = list(graph.actions.values())
        succeeded   = sum(1 for a in all_actions if a.status == ActionStatus.SUCCEEDED)
        failed      = sum(1 for a in all_actions if a.status == ActionStatus.FAILED)
        rolled_back = sum(1 for a in all_actions if a.status == ActionStatus.ROLLED_BACK)

        analytics_data = self._analytics.summary()
        self._rollback.cleanup()

        return SchedulerResult(
            success=failed == 0,
            mission=graph.mission,
            total=len(all_actions),
            succeeded=succeeded,
            failed=failed,
            rolled_back=rolled_back,
            elapsed_ms=elapsed_ms,
            outputs=outputs,
            analytics=analytics_data,
        )

    # ── Layer execution ───────────────────────────────────────────────

    def _run_layer(self, actions: list[Action]) -> list[tuple[Action, ActionResult]]:
        results: list[tuple[Action, ActionResult]] = []

        with ThreadPoolExecutor(
            max_workers=min(len(actions), self._max_workers),
            thread_name_prefix="ual-worker",
        ) as pool:
            futures = {pool.submit(self._execute_action, a): a for a in actions}
            for fut in as_completed(futures):
                action = futures[fut]
                try:
                    result = fut.result()
                except Exception as exc:
                    result = ActionResult(
                        success=False,
                        error=f"Unexpected scheduler error: {exc}",
                        executor_used="scheduler",
                    )
                    action.mark_failed(result)
                results.append((action, result))

        return results

    # ── Single action ─────────────────────────────────────────────────

    def _execute_action(self, action: Action) -> ActionResult:
        self._analytics.action_started()
        self._emit("running", action)

        # Snapshot before execution
        self._rollback.snapshot(action)

        # Select executor
        exec_start = time.time()
        executor = self._selector.select(action)
        memory_informed = self._memory.success_rate(
            executor.name if executor else "", action.goal
        ) >= 0
        if executor is None:
            result = ActionResult(
                success=False,
                error=f"No executor can handle: {action.goal}",
                executor_used="none",
            )
            action.mark_failed(result)
            self._finish_action(action, result, 0.0, 0.0, 0.0, memory_informed)
            return result

        action.mark_running(executor.name)

        # Execute
        ex_start = time.time()
        result   = executor.execute(action)
        ex_ms    = (time.time() - ex_start) * 1000

        # Verify
        v_start  = time.time()
        result   = self._verifier.verify(action, result)
        v_ms     = (time.time() - v_start) * 1000

        # Rollback on failure
        rb_ms = 0.0
        if not result.success:
            rb_start = time.time()
            self._rollback.rollback(action)
            rb_ms = (time.time() - rb_start) * 1000
            action.mark_failed(result)
            self._emit("failed", action)
        else:
            action.mark_succeeded(result)
            self._emit("succeeded", action)

        action_ms = (time.time() - exec_start) * 1000
        self._finish_action(action, result, ex_ms, v_ms, rb_ms, memory_informed, action_ms)
        return result

    def _finish_action(
        self,
        action: Action,
        result: ActionResult,
        ex_ms: float,
        v_ms: float,
        rb_ms: float,
        memory_informed: bool,
        action_ms: float = 0.0,
    ) -> None:
        self._memory.record(action, result)
        self._analytics.record(ActionMetric(
            action_id=action.id,
            goal=action.goal,
            executor=result.executor_used,
            action_latency_ms=action_ms,
            executor_latency_ms=ex_ms,
            verification_latency_ms=v_ms,
            rollback_latency_ms=rb_ms,
            success=result.success,
            verified=result.verified,
            rolled_back=result.rolled_back,
            memory_informed=memory_informed,
        ))
        self._analytics.action_finished()

    def _emit(self, status: str, action: Action) -> None:
        logger.info("[UAL] %s '%s' (%s)", status.upper(), action.id, action.goal[:60])
        if self._on_progress:
            try:
                self._on_progress(action, status)
            except Exception:
                pass
