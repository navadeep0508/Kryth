"""ExecutionSupervisor — the central runtime operating system.

Coordinates all subsystems during a mission:

    supervisor.start(goal)        — initialize all subsystems
    supervisor.pre_step(cmd)      — predict + pre-check before executing
    supervisor.post_step(out, rc) — parse + recover after executing
    supervisor.monitor()          — periodic health sweep
    supervisor.complete(summary)  — record outcomes and reflect
    supervisor.should_stop()      — budget / confidence / health gate

Intended usage:

    supervisor = ExecutionSupervisor()
    supervisor.start("Build and test the Flask API")

    for step in plan.steps:
        pre = supervisor.pre_step(step.command)
        if pre.proactive_fix:
            shell.execute(pre.proactive_fix)

        out, rc = shell.execute(step.command, step.timeout)
        supervisor.post_step(step.command, out, rc)

        if supervisor.should_stop():
            break

    supervisor.complete({"actions": done_actions})
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MissionContext:
    goal: str
    project_root: str = "."
    token_limit: int = 0
    time_limit_seconds: float = 0.0
    agent_limit: int = 20
    auto_recover: bool = True
    autonomous_threshold: float = 0.90
    risk_threshold: str = "medium"   # low | medium | high — above this requires approval


@dataclass
class PreStepResult:
    should_proceed: bool
    proactive_fix: Optional[str]
    risk_level: str
    confidence_decision: str
    predicted_failure_probability: float
    warnings: List[str] = field(default_factory=list)


@dataclass
class PostStepResult:
    success: bool
    recovery_attempted: bool
    recovery_succeeded: bool
    error_class: str
    summary: str


class ExecutionSupervisor:
    """The central runtime controller for autonomous mission execution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._context: Optional[MissionContext] = None
        self._step_count: int = 0
        self._success_count: int = 0
        self._failure_count: int = 0
        self._start_time: float = 0.0
        self._stopped: bool = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running: bool = False

    # ------------------------------------------------------------------
    # Lifecycle

    def start(self, context: MissionContext) -> None:
        """Initialize all subsystems and begin the mission."""
        with self._lock:
            self._context = context
            self._step_count = 0
            self._success_count = 0
            self._failure_count = 0
            self._start_time = time.monotonic()
            self._stopped = False

        # Configure budget
        from agent.supervisor.budget import budget_controller
        budget_controller.reset()
        budget_controller.configure(
            token_limit=context.token_limit,
            time_limit_seconds=context.time_limit_seconds,
            agent_limit=context.agent_limit,
        )

        # Configure confidence
        from agent.supervisor.confidence import confidence_controller
        confidence_controller.configure(
            autonomous_threshold=context.autonomous_threshold
        )

        # Reset agent supervisor
        from agent.supervisor.agent_supervisor import agent_supervisor
        agent_supervisor.reset()

        # Reset replanner counters
        from agent.supervisor.replanner import dynamic_replanner
        # (no reset API needed — replanner accumulates history)

        # Emit mission start to UI
        try:
            from agent import ui
            ui.mission_start(context.goal)
            ui.timeline_event(f"Mission started: {context.goal}", "info")
        except Exception:
            pass

        # Start background monitoring
        self._start_monitor()

    def complete(self, summary: dict | None = None) -> None:
        """Mark the mission complete, record experience, reflect."""
        self._stop_monitor()
        summary = summary or {}

        # Compute final health
        try:
            from agent.supervisor.health import health_engine
            health = health_engine.snapshot()
            summary.setdefault("final_health", health.overall)
            summary.setdefault("risk_level", health.risk_level)
        except Exception:
            pass

        # Persist experience
        self._record_experience(success=True, summary=summary)

        # Reflect
        self._reflect(summary)

        # Emit to UI
        try:
            from agent import ui
            ui.mission_complete(summary)
            ui.timeline_event("Mission completed", "success")
        except Exception:
            pass

    def abort(self, reason: str) -> None:
        """Mission failed or was cancelled."""
        self._stop_monitor()
        self._record_experience(success=False, summary={"reason": reason})
        try:
            from agent import ui
            ui.mission_failed(reason)
            ui.timeline_event(f"Mission aborted: {reason}", "error")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Per-step hooks

    def pre_step(
        self,
        command: str,
        step_description: str = "",
    ) -> PreStepResult:
        """Run before executing a command: predict, check risk, maybe gate."""
        with self._lock:
            self._step_count += 1
            ctx = self._context

        warnings: List[str] = []

        # Budget check
        try:
            from agent.supervisor.budget import budget_controller
            if budget_controller.should_stop():
                return PreStepResult(
                    should_proceed=False,
                    proactive_fix=None,
                    risk_level="critical",
                    confidence_decision="block",
                    predicted_failure_probability=1.0,
                    warnings=["Budget exceeded — stopping execution"],
                )
        except Exception:
            pass

        # Ownership conflict check
        try:
            from agent.supervisor.ownership import ownership_enforcer
            # If a file is mentioned in the command and is owned, warn
            import re as _re
            files = _re.findall(r'[\w./\\-]+\.(?:py|js|ts|rs|go|java|rb|php)', command)
            for f in files[:3]:
                owner = ownership_enforcer.owner_of(f, "file")
                if owner:
                    warnings.append(f"File '{f}' is owned by agent '{owner}'")
        except Exception:
            pass

        # Risk scoring
        risk_level = "low"
        try:
            from agent.supervisor.risk import risk_engine
            risk_score = risk_engine.score_command(command)
            risk_level = risk_score.level.value
            if risk_score.factors:
                warnings.extend(risk_score.factors[:2])
        except Exception:
            pass

        # Confidence decision
        confidence_decision = "autonomous"
        try:
            from agent.supervisor.confidence import confidence_controller
            from agent.supervisor.prediction_engine import prediction_engine
            if ctx:
                pred = prediction_engine.predict_step(
                    step_description or command[:80],
                    project_root=ctx.project_root,
                    command=command,
                )
                conf_result = confidence_controller.decide(
                    confidence=1.0 - pred.failure_probability,
                    risk_level=risk_level,
                    action_description=command[:60],
                )
                confidence_decision = conf_result.decision.value
                failure_prob = pred.failure_probability
                proactive_fix = pred.proactive_fix if pred.can_auto_fix else None
            else:
                failure_prob = 0.20
                proactive_fix = None
        except Exception:
            failure_prob = 0.20
            proactive_fix = None

        should_proceed = confidence_decision != "block"

        return PreStepResult(
            should_proceed=should_proceed,
            proactive_fix=proactive_fix,
            risk_level=risk_level,
            confidence_decision=confidence_decision,
            predicted_failure_probability=failure_prob,
            warnings=warnings,
        )

    def post_step(
        self,
        command: str,
        output: str,
        exit_code: int,
        agent_id: Optional[str] = None,
        project_root: Optional[str] = None,
    ) -> PostStepResult:
        """Run after a command completes: parse, recover if needed."""
        root = project_root or (self._context.project_root if self._context else ".")

        if exit_code == 0:
            with self._lock:
                self._success_count += 1
            self._update_progress()
            return PostStepResult(
                success=True,
                recovery_attempted=False,
                recovery_succeeded=False,
                error_class="",
                summary="success",
            )

        with self._lock:
            self._failure_count += 1

        # Classify and recover
        ctx = self._context
        auto_recover = ctx.auto_recover if ctx else True

        try:
            from agent.supervisor.recovery_manager import recovery_manager, RecoveryClass
            error_class = recovery_manager.classify_error(output, exit_code)

            if auto_recover:
                event = recovery_manager.handle(
                    failure_class=error_class,
                    description=f"Command failed: {command[:60]}",
                    agent_id=agent_id,
                    command=command,
                    error_output=output[:1000],
                    project_root=root,
                )
                self._update_progress()
                return PostStepResult(
                    success=event.resolved,
                    recovery_attempted=True,
                    recovery_succeeded=event.resolved,
                    error_class=error_class.value,
                    summary=event.resolution,
                )
        except Exception as exc:
            pass

        self._update_progress()
        return PostStepResult(
            success=False,
            recovery_attempted=False,
            recovery_succeeded=False,
            error_class="unknown",
            summary=f"exit code {exit_code}",
        )

    # ------------------------------------------------------------------
    # Monitor

    def monitor(self) -> dict:
        """Run a single monitor sweep across all subsystems."""
        issues: List[str] = []

        # Health
        try:
            from agent.supervisor.health import health_engine
            health = health_engine.probe_and_publish()
            if health.overall < 50:
                issues.append(f"system health critical: {health.overall}%")
        except Exception:
            pass

        # Agent sweep
        try:
            from agent.supervisor.agent_supervisor import agent_supervisor
            agent_issues = agent_supervisor.sweep()
            issues.extend(agent_issues)
        except Exception:
            pass

        # Terminal sweep
        try:
            from agent.supervisor.terminal_supervisor import terminal_supervisor
            term_actions = terminal_supervisor.sweep()
            if term_actions:
                issues.extend(term_actions)
                try:
                    from agent import ui
                    for action in term_actions:
                        ui.timeline_event(action, "warn")
                except Exception:
                    pass
        except Exception:
            pass

        # Parallel optimization
        try:
            from agent.supervisor.parallel_optimizer import parallel_optimizer
            if self._context:
                opt = parallel_optimizer.recommend(
                    self._context.goal, self._context.project_root
                )
                if opt.should_reduce:
                    issues.append(f"parallel optimizer: {opt.reason}")
        except Exception:
            pass

        return {"issues": issues, "count": len(issues)}

    def should_stop(self) -> bool:
        """True when the mission should stop (budget, health, etc.)."""
        with self._lock:
            if self._stopped:
                return True
        try:
            from agent.supervisor.budget import budget_controller
            if budget_controller.should_stop():
                return True
        except Exception:
            pass
        return False

    def early_stop_if_complete(self, output: str) -> bool:
        """Heuristic: has the objective already been achieved?"""
        indicators = [
            "mission complete", "task complete", "objective achieved",
            "all tests pass", "successfully deployed", "build succeeded",
            "DONE", "COMPLETE",
        ]
        lower = output.lower()
        return any(ind.lower() in lower for ind in indicators)

    # ------------------------------------------------------------------
    # Background monitor thread

    def _start_monitor(self) -> None:
        with self._lock:
            if self._monitor_running:
                return
            self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="kryth-supervisor"
        )
        self._monitor_thread.start()

    def _stop_monitor(self) -> None:
        with self._lock:
            self._monitor_running = False

    def _monitor_loop(self) -> None:
        while True:
            with self._lock:
                if not self._monitor_running:
                    break
            try:
                self.monitor()
            except Exception:
                pass
            time.sleep(5.0)   # sweep every 5 seconds

    # ------------------------------------------------------------------
    # Internal helpers

    def _update_progress(self) -> None:
        try:
            from agent import ui
            with self._lock:
                total = max(1, self._step_count)
                success = self._success_count
                pct = int(success / total * 100) if total > 0 else 0
                ctx = self._context
            stage = f"{success}/{total} steps complete"
            ui.mission_progress(pct, stage)
        except Exception:
            pass

    def _record_experience(self, success: bool, summary: dict) -> None:
        with self._lock:
            ctx = self._context
        if not ctx:
            return
        try:
            from agent.experience import get_experience
            exp = get_experience(ctx.project_root)
            exp.learn(
                "task",
                title=ctx.goal[:80],
                summary=str(summary)[:500],
                success=success,
                importance=0.7,
                confidence=0.8,
                extra={
                    "step_count": self._step_count,
                    "failure_count": self._failure_count,
                    "elapsed": time.monotonic() - self._start_time,
                },
            )
        except Exception:
            pass

    def _reflect(self, summary: dict) -> None:
        with self._lock:
            steps = self._step_count
            failures = self._failure_count
            ctx = self._context

        worked = f"{steps - failures} of {steps} steps succeeded"
        failed_str = (
            f"{failures} step failures encountered"
            if failures > 0 else ""
        )
        learned = ""

        # Pull replanning insights
        try:
            from agent.supervisor.replanner import dynamic_replanner
            replan_count = dynamic_replanner.replan_count
            if replan_count:
                learned = f"Replanned {replan_count} time(s) during execution"
        except Exception:
            pass

        if worked or failed_str or learned:
            try:
                from agent import ui
                ui.reflection_show(
                    worked=worked,
                    failed=failed_str,
                    learned=learned,
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Convenience context manager

    def __enter__(self) -> "ExecutionSupervisor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._stop_monitor()
        if exc_type is not None:
            self.abort(str(exc_val))
        return False
