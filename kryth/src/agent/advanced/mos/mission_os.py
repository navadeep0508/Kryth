"""Mission Operating System — top-level portfolio manager.

The MOS manages MULTIPLE concurrent autonomous missions.
It coordinates the UniversalPlanner + ActionManager (UAL) + Executive
for each mission in isolation.

Strict layer separation:
  MissionOS  — portfolio management, scheduling, resource allocation
  Executive  — quality + risk supervision per mission
  Planner    — objective → ActionGraph per mission
  UAL        — executes ActionGraph per mission

The MOS MUST NOT bypass the Executive.
The MOS MUST NOT execute actions directly.
The MOS MUST NOT modify production without Executive approval.
"""

from __future__ import annotations

import logging

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agent.mos.mission_state import (
    Mission, MissionStatus, MissionPriorityLevel, ResourceBudget,
)
from agent.mos.mission_queue        import MissionQueue
from agent.mos.mission_priority     import MissionPriority
from agent.mos.mission_allocator    import MissionAllocator
from agent.mos.mission_scheduler    import MissionScheduler
from agent.mos.mission_dependencies import MissionDependencies
from agent.mos.mission_lifecycle    import MissionLifecycle
from agent.mos.mission_router       import MissionRouter
from agent.mos.mission_snapshot     import MissionSnapshot
from agent.mos.mission_recovery     import MissionRecovery
from agent.mos.mission_memory       import MissionMemory
from agent.mos.mission_analytics    import MissionAnalytics
from agent.mos.mission_dashboard    import push_portfolio
from agent.mos.portfolio_report     import PortfolioReport
from agent.mos.resource_manager     import ResourceManager, SystemResources

logger = logging.getLogger(__name__)


class MissionOS:
    """The KRYTH Mission Operating System.

    Manages a portfolio of concurrent autonomous missions, each running
    through the Planner → UAL → Executive pipeline.

    Quick start::

        from agent.mos import MissionOS

        mos = MissionOS()
        mid = mos.submit("Build SaaS authentication system", priority="high")
        mid2 = mos.submit("Deploy to staging", priority="normal", depends_on=[mid])

        # Run the portfolio (blocking)
        mos.run_until_done()

        report = mos.portfolio_report()
        print(report)
    """

    def __init__(
        self,
        project_root: str | Path = ".",
        max_concurrent: int = 4,
        system_resources: Optional[SystemResources] = None,
        tick_interval_s: float = 2.0,
        checkpoint_interval_s: float = 30.0,
    ) -> None:
        self._root               = Path(project_root).resolve()
        self._tick_interval      = tick_interval_s
        self._checkpoint_interval = checkpoint_interval_s

        # Sub-systems
        self._resources    = ResourceManager(system_resources)
        self._queue        = MissionQueue()
        self._deps         = MissionDependencies()
        self._allocator    = MissionAllocator(self._resources)
        self._scheduler    = MissionScheduler(
            self._queue, self._allocator, self._deps, self._resources,
            max_concurrent=max_concurrent,
        )
        self._lifecycle    = MissionLifecycle()
        self._router       = MissionRouter()
        self._snapshot     = MissionSnapshot(self._root)
        self._recovery     = MissionRecovery(self._snapshot)
        self._memory       = MissionMemory(self._root)
        self._analytics    = MissionAnalytics(self._root)
        self._reporter     = PortfolioReport(self._root)

        # Portfolio state
        self._portfolio: dict[str, Mission] = {}
        self._lock       = threading.RLock()
        self._running    = False
        self._stop_event = threading.Event()

        # Executors keyed by mission id — user-provided callables or built-in
        self._mission_runners: dict[str, Callable[[Mission], Any]] = {}

        # Recover from prior crash
        self._recover_on_startup()

    # ── Submit API ────────────────────────────────────────────────────

    def submit(
        self,
        goal: str,
        name: str = "",
        description: str = "",
        priority: str = "normal",
        tags: list[str] | None = None,
        depends_on: list[str] | None = None,
        deadline: Optional[str] = None,
        max_workers: int = 4,
        max_tokens: int = 200_000,
        max_api_calls: int = 500,
        metadata: dict | None = None,
    ) -> str:
        """Submit a new mission. Returns mission id."""
        mission = Mission(
            id=str(uuid.uuid4())[:12],
            name=name or goal[:40],
            description=description,
            goal=goal,
            priority=MissionPriorityLevel(priority),
            tags=tags or [],
            depends_on=depends_on or [],
            deadline=deadline,
            budget=ResourceBudget(
                max_workers=max_workers,
                max_tokens=max_tokens,
                max_api_calls=max_api_calls,
            ),
            metadata=metadata or {},
        )

        # Route to strategy and adjust budget
        routing = self._router.route(mission)
        mission.budget = self._router.adjust_budget(mission, routing)
        mission.metadata["routing_strategy"] = routing.strategy
        mission.metadata["planner_hint"]      = routing.planner_hint

        with self._lock:
            self._portfolio[mission.id] = mission
            self._scheduler.enqueue(mission)
            # Block mission if dependencies exist and they're not complete
            if mission.depends_on:
                for dep_id in mission.depends_on:
                    dep = self._portfolio.get(dep_id)
                    if dep and not dep.is_terminal():
                        blocked = self._lifecycle.block(mission, f"waiting for {dep_id}")
                        if blocked:
                            self._queue.remove(mission.id)
                        break

        self._checkpoint()
        logger.info("Submitted mission %s: %s [%s]", mission.id, mission.name, priority)
        return mission.id

    def cancel(self, mission_id: str, reason: str = "cancelled by user") -> bool:
        with self._lock:
            m = self._portfolio.get(mission_id)
            if not m or m.is_terminal():
                return False
            return self._lifecycle.fail(m, reason)

    def pause(self, mission_id: str) -> bool:
        with self._lock:
            m = self._portfolio.get(mission_id)
            if not m:
                return False
            return self._lifecycle.pause(m)

    def resume(self, mission_id: str) -> bool:
        with self._lock:
            m = self._portfolio.get(mission_id)
            if not m or m.status != MissionStatus.PAUSED:
                return False
            self._lifecycle.resume(m)
            self._scheduler.enqueue(m)
            return True

    def retry(self, mission_id: str) -> bool:
        with self._lock:
            m = self._portfolio.get(mission_id)
            if not m:
                return False
            if self._lifecycle.retry(m):
                self._scheduler.enqueue(m)
                return True
            return False

    # ── Register custom runner ─────────────────────────────────────────

    def register_runner(
        self,
        strategy: str,
        runner: Callable[[Mission], Any],
    ) -> None:
        """Register a callable that executes missions with the given routing strategy.

        The runner receives a Mission and must update mission.metrics / mission.result_summary.
        It is responsible for calling Executive.review() per action tick.
        """
        self._mission_runners[strategy] = runner

    # ── Portfolio run loop ────────────────────────────────────────────

    def run_until_done(self, timeout_s: float = 3600.0) -> dict:
        """Block until all missions are terminal (or timeout)."""
        self._running    = True
        self._stop_event.clear()
        start = time.time()
        last_checkpoint = start

        while not self._stop_event.is_set():
            self._tick()

            now = time.time()
            if now - last_checkpoint > self._checkpoint_interval:
                self._checkpoint()
                last_checkpoint = now

            if self._all_done():
                logger.info("MOS: all missions complete")
                break

            if now - start > timeout_s:
                logger.warning("MOS: timeout after %.0fs", timeout_s)
                break

            time.sleep(self._tick_interval)

        self._running = False
        return self.summary()

    def stop(self) -> None:
        """Request the run loop to stop gracefully."""
        self._stop_event.set()

    # ── Single tick ───────────────────────────────────────────────────

    def _tick(self) -> None:
        with self._lock:
            running = {mid: m for mid, m in self._portfolio.items()
                       if m.status == MissionStatus.RUNNING}

        newly_admitted = self._scheduler.tick(running)
        for mission in newly_admitted:
            self._start_mission_async(mission)

        # Refresh dashboard
        with self._lock:
            push_portfolio(self._portfolio, self._resources.utilization())

        self._analytics.record_snapshot(
            self._portfolio, self._resources.utilization()
        )

    def _start_mission_async(self, mission: Mission) -> None:
        """Launch mission execution in a background thread."""
        t = threading.Thread(
            target=self._run_mission,
            args=(mission,),
            name=f"mission-{mission.id}",
            daemon=True,
        )
        t.start()

    def _run_mission(self, mission: Mission) -> None:
        """Execute one mission end-to-end using the registered runner."""
        strategy = mission.metadata.get("routing_strategy", "planner_ual")
        runner   = self._mission_runners.get(strategy) or self._mission_runners.get("default")

        if runner is None:
            runner = self._default_runner

        try:
            logger.info("MOS: starting mission %s (%s)", mission.id, strategy)
            runner(mission)

            with self._lock:
                if not mission.is_terminal():
                    self._lifecycle.complete(mission, mission.result_summary or "mission completed")
                unblocked = self._scheduler.on_completed(mission, self._portfolio)
                if unblocked:
                    logger.info("MOS: unblocked missions: %s", unblocked)

        except Exception as exc:
            logger.exception("MOS: mission %s raised: %s", mission.id, exc)
            with self._lock:
                self._lifecycle.fail(mission, str(exc))
                self._scheduler.on_failed(mission, self._portfolio)
        finally:
            self._memory.record_mission(mission)
            self._snapshot.save(mission)

    def _default_runner(self, mission: Mission) -> None:
        """Minimal default runner — integrates Planner + UAL + Executive.

        Real deployments should register a custom runner per strategy.
        """
        try:
            from agent.planner.planner import UniversalPlanner
            from agent.action.action_manager import ActionManager
            from agent.executive.executive import Executive

            planner  = UniversalPlanner(project_root=str(self._root))
            manager  = ActionManager(project_root=str(self._root))
            exec_    = Executive(project_root=str(self._root))

            # 1. Plan
            hint    = mission.metadata.get("planner_hint", "")
            graph   = planner.plan(f"{hint}: {mission.goal}" if hint else mission.goal)

            mission.metrics.actions_total = len(graph.all())

            # 2. Executive pre-check
            state = exec_.review(
                session_id=mission.id,
                mission=mission.goal,
                health_kwargs={
                    "total_actions": mission.metrics.actions_total,
                    "succeeded": 0, "failed": 0,
                    "running": 0, "pending": mission.metrics.actions_total,
                    "skipped": 0, "replan_count": 0,
                },
            )
            from agent.executive.executive_state import ExecutiveDecision
            if state.recommendation == ExecutiveDecision.ABORT:
                self._lifecycle.fail(mission, f"Executive pre-check: ABORT — {state.rationale}")
                return

            # 3. Execute
            result = manager.run_graph(graph)

            # 4. Update mission metrics
            mission.metrics.actions_done   = result.succeeded
            mission.metrics.actions_failed = result.failed
            mission.metrics.confidence     = state.confidence.overall
            mission.metrics.risk_level     = state.risk.value
            mission.result_summary = (
                f"Succeeded {result.succeeded}/{result.total} actions. "
                f"Conf={state.confidence.overall:.0%} Risk={state.risk.value}"
            )

            # 5. Executive post-review
            exec_.review(
                session_id=mission.id,
                mission=mission.goal,
                health_kwargs={
                    "total_actions": result.total,
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "running": 0,
                    "pending": 0,
                    "skipped": result.skipped,
                    "replan_count": mission.metrics.replan_count,
                },
            )
            exec_.finalize(mission.id, elapsed_s=mission.elapsed_s())

        except ImportError as exc:
            # Planner/UAL/Executive not available — mark done with warning
            logger.warning("Default runner import error (running in stub mode): %s", exc)
            mission.result_summary = f"Stub execution (imports unavailable): {exc}"

    # ── Queries ───────────────────────────────────────────────────────

    def get(self, mission_id: str) -> Optional[Mission]:
        with self._lock:
            return self._portfolio.get(mission_id)

    def list_missions(self, status: Optional[str] = None) -> list[Mission]:
        with self._lock:
            missions = list(self._portfolio.values())
        if status:
            missions = [m for m in missions if m.status.value == status]
        return sorted(missions, key=lambda m: m.created_at)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            portfolio = dict(self._portfolio)
        from agent.mos.mission_state import MissionStatus as S
        def count(s): return sum(1 for m in portfolio.values() if m.status == s)
        return {
            "total":     len(portfolio),
            "running":   count(S.RUNNING),
            "queued":    count(S.QUEUED),
            "paused":    count(S.PAUSED),
            "blocked":   count(S.BLOCKED),
            "completed": count(S.COMPLETED),
            "failed":    count(S.FAILED),
            "archived":  count(S.ARCHIVED),
            "resources": self._resources.utilization(),
        }

    def portfolio_report(self, label: str = "") -> str:
        with self._lock:
            portfolio = dict(self._portfolio)
        return self._reporter.generate(portfolio, self._analytics, label)

    def analytics_summary(self) -> dict:
        return self._analytics.summary()

    def benchmark_metrics(self) -> dict[str, float]:
        return self._analytics.benchmark_metrics()

    # ── Internals ─────────────────────────────────────────────────────

    def _all_done(self) -> bool:
        with self._lock:
            return all(m.is_terminal() for m in self._portfolio.values())

    def _checkpoint(self) -> None:
        with self._lock:
            portfolio = dict(self._portfolio)
        count = self._recovery.checkpoint(portfolio)
        if count:
            logger.debug("MOS: checkpointed %d missions", count)

    def _recover_on_startup(self) -> None:
        recovered = self._recovery.recover()
        for m in recovered:
            with self._lock:
                self._portfolio[m.id] = m
            if m.status == MissionStatus.QUEUED:
                self._scheduler.enqueue(m)
        if recovered:
            logger.info("MOS: recovered %d missions on startup", len(recovered))
