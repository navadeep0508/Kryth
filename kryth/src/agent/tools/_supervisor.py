"""Supervisor tools — LLM-callable interface to the Execution Supervisor.

Tools:
  run_supervised_mission   — execute a mission with full supervision
  supervisor_status        — get current supervisor dashboard
  supervisor_predict       — predict failure risk for a command/plan
  supervisor_health        — get system health snapshot
  supervisor_replan        — trigger dynamic replan after a failure
  supervisor_recover       — manually trigger recovery for a failure
  ownership_claim          — claim a resource for an agent
  ownership_release        — release claimed resources
  ownership_status         — show current ownership table
  budget_status            — show current budget consumption
"""

from __future__ import annotations

import time
from typing import Optional

from agent.tools._results import err


def run_supervised_mission(
    goal: str,
    steps: list | None = None,
    token_limit: int = 0,
    time_limit_seconds: float = 0.0,
    agent_limit: int = 20,
    auto_recover: bool = True,
    project_root: str = ".",
) -> str:
    """Execute a mission under full Execution Supervisor oversight.

    The supervisor continuously monitors health, predicts failures,
    auto-recovers errors, dynamically replans when agents crash,
    enforces resource ownership, and records outcomes to the
    Experience Engine.

    Args:
        goal: Human-readable mission description.
        steps: Optional list of shell commands to execute under supervision.
               If omitted, only the supervisor context is initialized.
        token_limit: Max tokens before auto-stop (0 = no limit).
        time_limit_seconds: Max wall-clock seconds (0 = no limit).
        agent_limit: Max agent spawns allowed (default 20).
        auto_recover: Automatically attempt recovery on failures.
        project_root: Working directory for experience lookups.
    """
    from agent.supervisor.supervisor import ExecutionSupervisor, MissionContext

    ctx = MissionContext(
        goal=goal,
        project_root=project_root,
        token_limit=token_limit,
        time_limit_seconds=time_limit_seconds,
        agent_limit=agent_limit,
        auto_recover=auto_recover,
    )

    supervisor = ExecutionSupervisor()
    supervisor.start(ctx)

    if not steps:
        return (
            f"Execution Supervisor started for mission: {goal}\n"
            f"Use supervisor_status() to monitor. "
            f"Call run_supervised_mission with steps= to execute commands."
        )

    results = []
    for step in steps:
        if supervisor.should_stop():
            results.append(f"STOPPED: budget/health limit reached at step: {step}")
            break

        pre = supervisor.pre_step(step)

        # Apply proactive fix before the step
        if pre.proactive_fix:
            from agent.terminal.shell import get_shell
            shell = get_shell()
            fix_out, fix_rc = shell.execute(pre.proactive_fix, timeout=60.0)
            results.append(f"proactive fix: {pre.proactive_fix} → rc={fix_rc}")

        # Execute the step
        from agent.terminal.shell import get_shell
        shell = get_shell()
        out, rc = shell.execute(step, timeout=120.0)

        # Post-step supervision
        post = supervisor.post_step(step, out, rc, project_root=project_root)

        status = "✓" if post.success else "✗"
        line = f"{status} {step[:60]}"
        if not post.success:
            line += f" → {post.summary}"
        results.append(line)

        # Early stop if objective already achieved
        if rc == 0 and supervisor.early_stop_if_complete(out):
            results.append("(early stop: objective detected as complete)")
            break

    # Complete the mission
    actions = [r for r in results if r.startswith("✓")]
    supervisor.complete({
        "actions": [a[2:] for a in actions],
        "steps_total": len(steps),
        "steps_done": len(actions),
    })

    return "\n".join(results) if results else "mission complete (no steps)"


def supervisor_status() -> str:
    """Get the current Execution Supervisor dashboard."""
    try:
        from agent.supervisor.dashboard import render_supervisor_dashboard
        render_supervisor_dashboard()
        return "(supervisor dashboard rendered)"
    except Exception as exc:
        return err("EXEC_FAILED", f"supervisor dashboard error: {exc}")


def supervisor_predict(command: str, description: str = "", project_root: str = ".") -> str:
    """Predict failure risk for a command before executing it.

    Returns: failure probability, risk level, likely errors, suggested proactive fix.
    """
    try:
        from agent.supervisor.prediction_engine import prediction_engine
        from agent.supervisor.risk import risk_engine

        pred = prediction_engine.predict_step(
            description or command[:80],
            project_root=project_root,
            command=command,
        )
        risk = risk_engine.score_command(command)

        lines = [
            f"Command: {command[:70]}",
            f"Failure Probability: {pred.failure_probability:.0%}",
            f"Confidence: {pred.confidence:.0%}",
            f"Risk Level: {risk.level.value}",
        ]
        if pred.likely_errors:
            lines.append(f"Likely Errors: {', '.join(pred.likely_errors[:3])}")
        if pred.repair_commands:
            lines.append(f"Known Repairs: {', '.join(pred.repair_commands[:3])}")
        if pred.proactive_fix:
            lines.append(f"Proactive Fix: {pred.proactive_fix}")
        lines.append(f"Reasoning: {pred.reasoning[:120]}")

        return "\n".join(lines)
    except Exception as exc:
        return err("EXEC_FAILED", f"prediction error: {exc}")


def supervisor_health() -> str:
    """Get a real-time health snapshot of all supervisor subsystems."""
    try:
        from agent.supervisor.health import health_engine
        health = health_engine.probe_and_publish()

        lines = [f"Mission Health: {health.overall}% ({health.label})",
                 f"Risk Level: {health.risk_level}"]
        for name, sub in sorted(health.subsystems.items()):
            status = "healthy" if sub.is_healthy else ("degraded" if not sub.is_critical else "CRITICAL")
            lines.append(f"  {name.title()}: {sub.score}% ({status})"
                         + (f" — {sub.detail}" if sub.detail else ""))

        try:
            from agent.supervisor.budget import budget_controller
            snap = budget_controller.snapshot()
            lines.append(f"Budget: {budget_controller.format_summary()}")
        except Exception:
            pass

        return "\n".join(lines)
    except Exception as exc:
        return err("EXEC_FAILED", f"health check error: {exc}")


def supervisor_replan(
    reason: str,
    failed_agent: str = "",
    failed_step: str = "",
    available_agents: list | None = None,
    remaining_tasks: list | None = None,
    project_root: str = ".",
) -> str:
    """Trigger dynamic replanning after a runtime failure.

    Args:
        reason: Description of why replanning is needed.
        failed_agent: ID of the agent that crashed (if any).
        failed_step: Step that failed (if any).
        available_agents: Agent IDs available for reassignment.
        remaining_tasks: Task IDs or descriptions still pending.
    """
    try:
        from agent.supervisor.replanner import dynamic_replanner, ReplanEvent

        event = ReplanEvent(
            reason=reason,
            failed_agent=failed_agent or None,
            failed_step=failed_step or None,
            remaining_tasks=remaining_tasks or [],
            available_agents=available_agents or [],
        )
        result = dynamic_replanner.replan(event, project_root)

        lines = [
            f"Replan Result: {'SUCCESS' if result.success else 'FAILED'}",
            f"Strategy: {result.strategy}",
            f"Reason: {result.reason}",
        ]
        if result.new_assignments:
            lines.append("New Assignments:")
            for task, agent in result.new_assignments.items():
                lines.append(f"  {task} → {agent}")
        if result.skip_tasks:
            lines.append(f"Skipped: {', '.join(result.skip_tasks)}")
        if result.resume_from:
            lines.append(f"Resume from: {result.resume_from}")

        return "\n".join(lines)
    except Exception as exc:
        return err("EXEC_FAILED", f"replan error: {exc}")


def supervisor_recover(
    failure_type: str,
    description: str,
    command: str = "",
    error_output: str = "",
    agent_id: str = "",
    project_root: str = ".",
) -> str:
    """Manually trigger recovery for a specific failure.

    failure_type: agent_crash | terminal_hang | browser_drop |
                  dependency_missing | permission_denied | timeout |
                  merge_conflict | unknown
    """
    try:
        from agent.supervisor.recovery_manager import recovery_manager, RecoveryClass
        try:
            cls = RecoveryClass(failure_type)
        except ValueError:
            cls = RecoveryClass.UNKNOWN

        event = recovery_manager.handle(
            failure_class=cls,
            description=description,
            agent_id=agent_id or None,
            command=command or None,
            error_output=error_output or None,
            project_root=project_root,
        )

        status = "resolved" if event.resolved else "unresolved"
        return (
            f"Recovery {status}\n"
            f"Class: {event.failure_class.value}\n"
            f"Resolution: {event.resolution}"
            + (f"\nRecovery command: {event.recovery_command}" if event.recovery_command else "")
        )
    except Exception as exc:
        return err("EXEC_FAILED", f"recovery error: {exc}")


def ownership_claim(agent_id: str, resource: str, kind: str = "file") -> str:
    """Claim ownership of a resource for an agent.

    Prevents other agents from writing to this resource.
    kind: file | symbol | directory | port | browser | terminal
    """
    try:
        from agent.supervisor.ownership import ownership_enforcer, ConflictError
        try:
            ownership_enforcer.claim(agent_id, resource, kind)
            return f"claimed: {kind}:{resource} by {agent_id}"
        except ConflictError as e:
            return err("INVALID_STATE", str(e))
    except Exception as exc:
        return err("EXEC_FAILED", f"ownership claim error: {exc}")


def ownership_release(agent_id: str, resource: str = "", kind: str = "file") -> str:
    """Release an ownership lock. Pass resource='' to release all."""
    try:
        from agent.supervisor.ownership import ownership_enforcer
        if not resource:
            released = ownership_enforcer.release_all(agent_id)
            return f"released {len(released)} locks for {agent_id}"
        ownership_enforcer.release(agent_id, resource, kind)
        return f"released: {kind}:{resource} from {agent_id}"
    except Exception as exc:
        return err("EXEC_FAILED", f"ownership release error: {exc}")


def ownership_status() -> str:
    """Show the current resource ownership table."""
    try:
        from agent.supervisor.ownership import ownership_enforcer
        return ownership_enforcer.format_table()
    except Exception as exc:
        return err("EXEC_FAILED", f"ownership status error: {exc}")


def budget_status() -> str:
    """Show current budget consumption and limits."""
    try:
        from agent.supervisor.budget import budget_controller
        snap = budget_controller.snapshot()
        lines = [
            f"Tokens spent: {snap.tokens_spent:,}"
            + (f" / {snap.token_limit:,}" if snap.token_limit else ""),
            f"Elapsed: {snap.elapsed_seconds:.0f}s"
            + (f" / {snap.time_limit_seconds:.0f}s" if snap.time_limit_seconds else ""),
            f"Agents spawned: {snap.agent_spawns}"
            + (f" / {snap.agent_limit}" if snap.agent_limit else ""),
            f"Tool calls: {snap.tool_calls}",
        ]
        if snap.any_exceeded:
            lines.append("WARNING: budget exceeded")
        elif snap.token_pct > 0.75 or snap.time_pct > 0.75:
            lines.append("NOTICE: >75% of budget consumed")
        return "\n".join(lines)
    except Exception as exc:
        return err("EXEC_FAILED", f"budget status error: {exc}")
