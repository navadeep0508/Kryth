"""Dynamic Parallel Scheduler — spawns agents according to the DAG layers.

Workers are created one layer at a time:
  - Layer 1 (no deps) starts immediately
  - Each subsequent layer starts only when its dependencies complete
  - Within a layer, agents run in parallel (ThreadPoolExecutor)

Enhancements over the original:
  - Ownership enforcement: agents acquire file/dir locks before running
  - Work stealing: idle agents pick up tasks from failed/slow siblings
  - Failure recovery: failed agents are retried once via WorkQueue
  - Dynamic worker count: scales with team size and cost estimate
  - Agent lifecycle events: AGENT_CREATED, AGENT_TASK_START, etc.
"""
from __future__ import annotations

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from agent.orchestration.task_dag import TaskDAG
from agent.orchestration.team_generator import AgentRole, TeamPlan
from agent.orchestration.work_queue import WorkQueue, WorkItem


@dataclass
class WorkerResult:
    agent_id: str
    role: str
    success: bool
    output: str
    turns_used: int = 0
    error: str = ""


@dataclass
class SchedulerResult:
    success: bool
    outputs: Dict[str, WorkerResult] = field(default_factory=dict)
    final_output: str = ""
    total_turns: int = 0
    failed_agents: List[str] = field(default_factory=list)


ProgressCallback = Callable[[str, str, str], None]   # (agent_id, role, status)


def _dynamic_worker_count(team_size: int, cost_estimate: int) -> int:
    """Scale workers to match actual available parallelism.

    Parallel-first: use as many workers as the team has agents, bounded
    only by CPU cores (so we don't thrash a 2-core machine with 32 threads).
    Token cost no longer reduces worker count — agents are I/O bound on
    the LLM network call, not CPU bound.
    """
    import os
    cpu_cores = max(4, os.cpu_count() or 8)
    # Allow up to 2× CPU cores since agents spend most time waiting on LLM I/O
    return min(team_size, cpu_cores * 2, 64)


def _build_agent_system_prompt(
    agent: AgentRole,
    dag: TaskDAG,
    project_context: str,
    prior_outputs: Dict[str, str],
    bus=None,
) -> str:
    task_descriptions = []
    for nid in agent.task_node_ids:
        node = dag.nodes.get(nid)
        if node:
            task_descriptions.append(f"- {node.name}: {node.description}")
            if node.validation:
                task_descriptions.append(f"  Validation: {'; '.join(node.validation)}")

    tasks_block = "\n".join(task_descriptions) or "Complete your assigned work."

    prior_block = ""
    if prior_outputs:
        parts = []
        for aid, out in prior_outputs.items():
            if out.strip():
                parts.append(f"=== {aid} output ===\n{out[:2000]}")
        if parts:
            prior_block = "\n\nContext from completed agents:\n" + "\n\n".join(parts)

    validation_block = ""
    if agent.validation_rules:
        rules = "\n".join(f"- {r}" for r in agent.validation_rules)
        validation_block = f"\n\nValidation requirements:\n{rules}"

    recovery_block = ""
    if agent.recovery_rules:
        rules = "\n".join(f"- {r}" for r in agent.recovery_rules)
        recovery_block = f"\n\nRollback strategy:\n{rules}"

    # Inject sibling events from the team bus
    bus_block = ""
    if bus is not None:
        try:
            events = bus.poll(agent.id)
            if events:
                bus_block = "\n\n" + bus.format_for_prompt(events)
        except Exception:
            pass

    return f"""You are a specialized engineering agent: {agent.role.upper()}

MISSION: {agent.mission}

ASSIGNED TASKS:
{tasks_block}

STRICT RULES:
1. Focus ONLY on your assigned tasks. Do not work on other areas.
2. Call tools and implement code — do not describe what you WOULD do.
3. When complete, summarize EXACTLY what you built with file paths.
4. Start your final message with: AGENT_COMPLETE: {agent.id}

PROJECT CONTEXT:
{project_context[:3000] if project_context else "(none)"}
{prior_block}{validation_block}{recovery_block}{bus_block}"""


def _run_single_agent(
    agent: AgentRole,
    dag: TaskDAG,
    project_context: str,
    prior_outputs: Dict[str, str],
    max_turns: int,
    ownership_mgr=None,
    bus=None,
) -> WorkerResult:
    """Run one agent, acquiring ownership locks for its directories first."""
    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    # Acquire file/dir ownership locks before starting
    lock_keys = (
        [f"FILE:{f}" for f in agent.owns.files]
        + [f"DIR:{d}" for d in agent.owns.directories]
    )
    if ownership_mgr and lock_keys:
        ownership_mgr.acquire_all(lock_keys)

    prompt = _build_agent_system_prompt(agent, dag, project_context, prior_outputs, bus=bus)
    parent = get_session()
    nested = _build_nested(agent.role, prompt, parent.depth)
    nested.system_prompt = prompt
    nested._agent_role = agent.role   # shown in live progress spinner
    nested._agent_id   = agent.id    # used by MC dashboard lookup
    if not nested.messages:
        nested.messages = [{"role": "system", "content": prompt}]
    nested.messages.append({"role": "user", "content": f"Begin your work: {agent.mission}"})

    token = push_session(nested)
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        content = getattr(result, "content", "") or ""
        turns = getattr(result, "turns_used", 0)
        # Treat interrupted/api_error as failure so the scheduler can retry.
        # "done" and "max_turns" are both acceptable completions — max_turns
        # means partial output is available and better than nothing.
        status = getattr(result, "status", "done")
        success = status not in ("interrupted", "api_error")
        error = f"agent status: {status}" if not success else ""

        # Self-healing: on failure, attempt automated repair before giving up
        if not success:
            try:
                from agent.orchestration.repair_loop import attempt_repair
                repair = attempt_repair(
                    agent_id=agent.id,
                    role=agent.role,
                    mission=agent.mission,
                    original_error=error,
                    project_context=prior_outputs.get("__context__", ""),
                    max_turns=min(30, max_turns // 2),
                )
                if repair.success:
                    return WorkerResult(
                        agent_id=agent.id, role=agent.role,
                        success=True, output=repair.output,
                        turns_used=turns + getattr(repair, "attempts", 0) * 5,
                    )
            except Exception:
                pass  # repair unavailable — fall through to failure

        return WorkerResult(
            agent_id=agent.id, role=agent.role,
            success=success, output=content, turns_used=turns, error=error,
        )
    except Exception as exc:
        return WorkerResult(
            agent_id=agent.id, role=agent.role,
            success=False, output="", error=str(exc),
        )
    finally:
        pop_session(token)
        if ownership_mgr and lock_keys:
            ownership_mgr.release_all(lock_keys)


def _run_integrator(
    prior_outputs: Dict[str, str],
    dag: TaskDAG,
    project_context: str,
    user_input: str,
    max_turns: int,
) -> WorkerResult:
    outputs_text = "\n\n".join(
        f"=== {aid} ===\n{out}" for aid, out in prior_outputs.items()
    )

    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    prompt = f"""You are the INTEGRATOR for a parallel engineering project.

ORIGINAL REQUEST: {user_input}

COMPONENT OUTPUTS:
{outputs_text[:6000]}

Your job:
1. Review all component outputs above
2. Identify any missing connections, import issues, or configuration gaps
3. Create any necessary glue code, shared config, or setup files
4. Ensure the overall system is cohesive and functional
5. Produce a final summary starting with: INTEGRATION_COMPLETE

Do NOT rewrite existing work — only add missing glue, fix imports, and surface a clear summary."""

    parent = get_session()
    nested = _build_nested("Integrator", prompt, parent.depth)
    nested.system_prompt = prompt
    nested.messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Review all component outputs and integrate the system."},
    ]

    token = push_session(nested)
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        content = getattr(result, "content", "") or ""
        return WorkerResult(agent_id="integrator", role="Integrator", success=True, output=content)
    except Exception as exc:
        return WorkerResult(agent_id="integrator", role="Integrator", success=False, output="", error=str(exc))
    finally:
        pop_session(token)


def _agent_execution_layers(agents: List[AgentRole]) -> List[List[AgentRole]]:
    """Topological sort of agents by their inter-agent dependency graph.

    Returns a list of layers where each layer's agents have all their
    upstream dependencies satisfied by previous layers. Agents within
    a layer are independent and can run in parallel.

    Uses Kahn's algorithm — same as dag.layers() but at the agent level.
    Agents with unknown/missing dependencies are treated as ready.
    """
    agent_map = {a.id: a for a in agents}
    # indegree = number of deps that must complete before this agent can start
    indegree: Dict[str, int] = {a.id: 0 for a in agents}
    for a in agents:
        for dep in a.dependencies:
            if dep in agent_map:  # only count known deps
                indegree[a.id] += 1

    layers: List[List[AgentRole]] = []
    completed: Set[str] = set()
    remaining = list(agents)

    while remaining:
        # Agents whose all dependencies are now satisfied
        ready = [
            a for a in remaining
            if all(
                dep not in agent_map or dep in completed
                for dep in a.dependencies
            )
        ]
        if not ready:
            # Circular deps or all-unknown deps — run everything left together
            ready = remaining[:]
        layers.append(ready)
        for a in ready:
            completed.add(a.id)
        ready_ids = {a.id for a in ready}
        remaining = [a for a in remaining if a.id not in ready_ids]

    return layers


def run_schedule(
    dag: TaskDAG,
    team: TeamPlan,
    strategy: str,
    project_context: str = "",
    user_input: str = "",
    max_turns_per_agent: int = 80,
    max_workers: int = 4,
    on_progress: Optional[ProgressCallback] = None,
) -> SchedulerResult:
    """Execute the team according to the DAG and strategy.

    Ownership locks prevent concurrent writes to the same directory.
    A WorkQueue enables work stealing: after completing its assigned task,
    an idle agent picks up any pending tasks left by failed siblings.
    """
    from agent import ui

    # Lazy import ownership manager from tool_scheduler
    try:
        from agent.tool_scheduler import _OwnershipManager
        ownership = _OwnershipManager()
    except Exception:
        ownership = None

    # Create team event bus and subscribe all agents
    try:
        from agent.orchestration.team_event_bus import TeamEventBus
        bus = TeamEventBus()
        for a in team.agents:
            bus.subscribe(a.id)
    except Exception:
        bus = None

    # Emit AGENT_CREATED for each agent
    try:
        from agent.ui import agent_created
        for a in team.agents:
            agent_created(a.id, a.role, len(a.task_node_ids))
    except Exception:
        pass

    # Dynamic worker count
    est_tokens = team.estimated_total_tokens
    n_workers = _dynamic_worker_count(len(team.agents), est_tokens)
    effective_workers = min(max_workers, n_workers)

    agent_map: Dict[str, AgentRole] = {a.id: a for a in team.agents}
    completed_ids: Set[str] = set()
    outputs: Dict[str, WorkerResult] = {}
    prior_outputs: Dict[str, str] = {}
    failed: List[str] = []
    prior_lock = threading.Lock()

    # ── Live Dashboard — owned by the scheduler (main) thread ────────────────
    # Rich Live must be driven from the thread that owns the terminal. Agent
    # workers run in ThreadPoolExecutor threads and only push events; the
    # scheduler loop calls live.update() between layers and after each agent
    # completes. No background thread needed.
    _live = None
    _dash_started = False
    if len(team.agents) > 1:
        try:
            from agent.ui.dashboard import (
                start_dashboard, push_event, get_active,
                _build_dashboard_panel, _process_events, _lock as _dlck, _state as _dstate,
            )
            from agent.ui.console import console as _rich_con
            from rich.live import Live

            # Stop spinner so we can own the terminal
            try:
                from agent.ui.renderer import _activity
                _activity._stop_cycler()
                _sp = getattr(_activity, "_spinner", None)
                if _sp:
                    _sp.stop()
            except Exception:
                pass

            # Initialise dashboard state (no thread needed)
            start_dashboard(
                goal=user_input[:60] or "Mission",
                total_agents=len(team.agents),
                total_layers=len(agent_layers),
            )
            push_event("timeline", message=f"Spawned {len(team.agents)} agents")

            _live = Live(
                _build_dashboard_panel(),
                console=_rich_con,
                refresh_per_second=4,
                transient=True,
                vertical_overflow="visible",
            )
            _live.__enter__()

            # Redirect console.print so logs go above the panel
            _orig_print = _rich_con.print
            def _live_print(*args, **kwargs):
                try:
                    _live.console.print(*args, **kwargs)
                except Exception:
                    try:
                        _orig_print(*args, **kwargs)
                    except Exception:
                        pass
            _rich_con.print = _live_print
            _dash_started = True
        except Exception:
            _live = None
            _dash_started = False

    def _dash_update():
        """Refresh the Live panel — call after any state change."""
        if _live is not None:
            try:
                from agent.ui.dashboard import _build_dashboard_panel, _process_events
                _process_events()
                _live.update(_build_dashboard_panel())
            except Exception:
                pass

    def _dash_stop():
        """Tear down the Live display and restore console.print."""
        nonlocal _live
        if _live is not None:
            try:
                from agent.ui.dashboard import push_event, stop_dashboard
                push_event("progress", percent=100)
                _process_events()
                _live.update(_build_dashboard_panel())
            except Exception:
                pass
            try:
                _live.__exit__(None, None, None)
            except Exception:
                pass
            try:
                _rich_con.print = _orig_print
            except Exception:
                pass
            _live = None
            try:
                stop_dashboard()
            except Exception:
                pass

    # Legacy MC fallback (only if Live init failed)
    _mc = None
    if not _dash_started and len(team.agents) > 1:
        try:
            from agent.ui.mission_control import MissionControl
            _mc = MissionControl(
                goal=user_input[:60] or "Mission",
                total_agents=len(team.agents),
                total_layers=len(agent_layers),
            )
            _mc.start()
        except Exception:
            _mc = None

    def _notify(aid: str, role: str, status: str) -> None:
        if on_progress:
            on_progress(aid, role, status)

        # Push to Live dashboard
        if _dash_started:
            try:
                from agent.ui.dashboard import push_event
                push_event("agent_update", id=aid, role=role, status=status)
                if status == "running":
                    push_event("timeline", message=f"{role} started")
                elif status == "done":
                    push_event("timeline", message=f"{role} done")
                elif status == "failed":
                    push_event("timeline", message=f"{role} failed")
                _dash_update()
            except Exception:
                pass

        # Update legacy Rich MC panel
        if _mc is not None:
            try:
                task_hint = {
                    "running": f"working on {aid}",
                    "done": "completed",
                    "failed": "failed",
                    "repairing": "self-healing…",
                }.get(status, status)
                _mc.set_agent(aid, role, status, task_hint)
            except Exception:
                pass

        # Plain text fallback
        if not _dash_started and _mc is None:
            if status == "running":
                ui.muted(f"  ◈ {role}")
            elif status == "done":
                ui.muted(f"  ✓ {role}")
            elif status == "failed":
                ui.warn(f"  ✗ {role} failed")
        try:
            from agent.ui import agent_task_start, agent_task_done, agent_failed
            if status == "running":
                agent_task_start(aid, aid, role)
            elif status == "done":
                agent_task_done(aid, aid, 0)
            elif status == "failed":
                agent_failed(aid, role, "execution error")
        except Exception:
            pass

    def _broadcast_events(agent_id: str, output: str) -> None:
        """Scan agent output for EVENT: lines and broadcast to sibling agents."""
        if bus is None or not output:
            return
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("EVENT:"):
                detail = stripped[6:].strip()
                try:
                    bus.publish(agent_id, "discovery", {"detail": detail})
                except Exception:
                    pass

    # Single-agent fast path
    if strategy == "single" or len(team.agents) == 1:
        agent = team.agents[0]
        _notify(agent.id, agent.role, "running")
        result = _run_single_agent(agent, dag, project_context, {}, max_turns_per_agent, ownership, bus)
        outputs[agent.id] = result
        if not result.success:
            failed.append(agent.id)
        _notify(agent.id, agent.role, "done" if result.success else "failed")
        return SchedulerResult(
            success=not failed, outputs=outputs,
            final_output=result.output,
            total_turns=result.turns_used,
            failed_agents=failed,
        )

    # Build work queue for stealing
    work_queue = WorkQueue()

    # Compute execution layers from AGENT dependencies (not DAG node layers).
    # This is the correct approach: agents are ordered by their inter-agent
    # dependencies (agent A depends on agent B → B runs first). Using DAG
    # node layers instead caused agents to be found in Layer 1 and then
    # silently skipped in later layers when their task_node_ids spanned layers.
    agent_layers = _agent_execution_layers(team.agents)
    n_layers = len(agent_layers)

    for layer_idx, layer_agents in enumerate(agent_layers, 1):
        if not layer_agents:
            continue

        # Always print a visible layer header — user must see what's running
        agent_names = ", ".join(a.role for a in layer_agents)
        parallel_note = " [parallel]" if len(layer_agents) > 1 else ""
        ui.muted(f"\n  ◈ Layer {layer_idx}/{n_layers}{parallel_note} — {agent_names}")

        # Update dashboard layer + progress
        pct = int((layer_idx - 1) * 90 / max(n_layers, 1))
        if _dash_started:
            try:
                from agent.ui.dashboard import push_event
                push_event("layer", current=layer_idx)
                push_event("progress", percent=pct)
                push_event("timeline", message=f"Layer {layer_idx}: {agent_names[:50]}")
                _dash_update()
            except Exception:
                pass
        if _mc is not None:
            _mc.set_layer(layer_idx)
            _mc.set_progress(pct)

        # Pre-load this layer into the work queue
        for ag in layer_agents:
            work_queue.submit(
                ag.id, ag.role,
                _build_agent_system_prompt(ag, dag, project_context, prior_outputs),
                max_turns_per_agent,
                priority=len(ag.dependencies),
            )

        # Parallel-first: run in parallel whenever there are multiple agents
        # in this layer, regardless of the declared strategy.  The strategy
        # field only defaults "sequential" when the DAG is strictly linear
        # (every layer has exactly 1 agent); multi-agent layers always fan out.
        if len(layer_agents) > 1:
            # Capture context ONCE here; each worker gets its OWN copy via
            # ctx.copy() so push_session / pop_session are fully isolated.
            # Using the same ctx.run() for all workers would make them share
            # the ContextVar slot, causing get_session() to return the wrong
            # nested session and failing immediately.
            _parent_ctx = contextvars.copy_context()

            def _worker(ag: AgentRole) -> WorkerResult:
                with prior_lock:
                    snapshot = dict(prior_outputs)
                result = _parent_ctx.copy().run(
                    _run_single_agent, ag, dag, project_context,
                    snapshot, max_turns_per_agent, ownership, bus,
                )
                _broadcast_events(ag.id, result.output)
                if result.success:
                    work_queue.complete(ag.id, result.output)
                else:
                    work_queue.fail(ag.id, result.error, retry=True)
                    # Work stealing: pick up a pending task if available
                    stolen = work_queue.try_steal()
                    if stolen:
                        steal_agent = agent_map.get(stolen.task_id)
                        if steal_agent:
                            try:
                                from agent.ui import work_stolen
                                work_stolen(ag.id, stolen.task_id)
                            except Exception:
                                pass
                            steal_result = _parent_ctx.copy().run(
                                _run_single_agent, steal_agent, dag, project_context,
                                snapshot, max_turns_per_agent, ownership, bus,
                            )
                            _broadcast_events(steal_agent.id, steal_result.output)
                            work_queue.complete(stolen.task_id, steal_result.output)
                            return steal_result
                return result

            with ThreadPoolExecutor(max_workers=effective_workers, thread_name_prefix="kryth-agent") as pool:
                for agent in layer_agents:
                    _notify(agent.id, agent.role, "running")

                futures = {pool.submit(_worker, ag): ag for ag in layer_agents}

                for future in as_completed(futures):
                    ag = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = WorkerResult(
                            agent_id=ag.id, role=ag.role,
                            success=False, output="", error=str(exc),
                        )
                    outputs[ag.id] = result
                    completed_ids.add(ag.id)
                    with prior_lock:
                        prior_outputs[ag.id] = result.output
                    if not result.success:
                        failed.append(ag.id)
                    _notify(ag.id, ag.role, "done" if result.success else "failed")

        else:
            for agent in layer_agents:
                _notify(agent.id, agent.role, "running")
                result = _run_single_agent(
                    agent, dag, project_context, prior_outputs,
                    max_turns_per_agent, ownership, bus,
                )
                _broadcast_events(agent.id, result.output)
                outputs[agent.id] = result
                completed_ids.add(agent.id)
                prior_outputs[agent.id] = result.output
                if not result.success:
                    failed.append(agent.id)
                    work_queue.fail(agent.id, result.error, retry=False)
                else:
                    work_queue.complete(agent.id, result.output)
                _notify(agent.id, agent.role, "done" if result.success else "failed")

    # ── No-idle drain: steal any pending tasks before stopping ────────────────
    # After the last layer's workers finish, drain anything still in the queue
    # (work-steal failures may have re-queued tasks). Run them sequentially now.
    _steal_rounds = 0
    while not work_queue.is_empty() and _steal_rounds < 20:
        _steal_rounds += 1
        stolen = work_queue.try_steal()
        if stolen is None:
            break
        steal_agent = agent_map.get(stolen.task_id)
        if steal_agent and steal_agent.id not in completed_ids:
            _notify(steal_agent.id, steal_agent.role, "running")
            result = _run_single_agent(
                steal_agent, dag, project_context, prior_outputs,
                max_turns_per_agent, ownership, bus,
            )
            _broadcast_events(steal_agent.id, result.output)
            outputs[steal_agent.id] = result
            completed_ids.add(steal_agent.id)
            prior_outputs[steal_agent.id] = result.output
            if result.success:
                work_queue.complete(stolen.task_id, result.output)
            else:
                failed.append(steal_agent.id)
            _notify(steal_agent.id, steal_agent.role, "done" if result.success else "failed")
        else:
            work_queue.complete(stolen.task_id, "")

    # Stop dashboards before integrator
    if _dash_started:
        try:
            from agent.ui.dashboard import push_event
            push_event("progress", percent=95)
            push_event("timeline", message="Integration phase")
            _dash_update()
        except Exception:
            pass
        _dash_stop()
    if _mc is not None:
        _mc.set_progress(95)
        _mc.stop()
        _mc = None

    # Skip integrator for single-layer or single-agent outcomes
    if n_layers > 1 and len(outputs) > 1:
        int_result = _run_integrator(
            prior_outputs, dag, project_context, user_input, max_turns_per_agent
        )
        outputs["integrator"] = int_result
        final = int_result.output
    else:
        final = next((r.output for r in outputs.values() if r.output), "")

    total_turns = sum(r.turns_used for r in outputs.values())

    return SchedulerResult(
        success=not failed,
        outputs=outputs,
        final_output=final,
        total_turns=total_turns,
        failed_agents=failed,
    )
