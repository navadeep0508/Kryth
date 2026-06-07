"""Dynamic Parallel Scheduler — spawns agents according to the DAG layers.

Workers are created one layer at a time:
  - Layer 1 (no deps) starts immediately
  - Each subsequent layer starts only when its dependencies complete
  - Within a layer, agents run in parallel (ThreadPoolExecutor)

No fixed teams. Workers are destroyed after they complete.
"""
from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from agent.orchestration.task_dag import TaskDAG, TaskNode
from agent.orchestration.team_generator import AgentRole, TeamPlan


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


def _build_agent_system_prompt(
    agent: AgentRole,
    dag: TaskDAG,
    project_context: str,
    prior_outputs: Dict[str, str],
) -> str:
    """Build the system prompt for a dynamically generated agent."""
    # Collect task descriptions from the DAG nodes this agent owns
    task_descriptions = []
    for nid in agent.task_node_ids:
        node = dag.nodes.get(nid)
        if node:
            task_descriptions.append(f"- {node.name}: {node.description}")
            if node.validation:
                task_descriptions.append(
                    f"  Validation: {'; '.join(node.validation)}"
                )

    tasks_block = "\n".join(task_descriptions) or "Complete your assigned work."

    # Prior context from already-completed agents
    prior_block = ""
    if prior_outputs:
        parts = []
        for aid, out in prior_outputs.items():
            if out.strip():
                parts.append(f"=== {aid} output ===\n{out[:2000]}")
        if parts:
            prior_block = "\n\nContext from completed agents:\n" + "\n\n".join(parts)

    # Validation rules
    validation_block = ""
    if agent.validation_rules:
        rules = "\n".join(f"- {r}" for r in agent.validation_rules)
        validation_block = f"\n\nValidation requirements:\n{rules}"

    # Recovery rules
    recovery_block = ""
    if agent.recovery_rules:
        rules = "\n".join(f"- {r}" for r in agent.recovery_rules)
        recovery_block = f"\n\nRollback strategy:\n{rules}"

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
{prior_block}{validation_block}{recovery_block}"""


def _run_single_agent(
    agent: AgentRole,
    dag: TaskDAG,
    project_context: str,
    prior_outputs: Dict[str, str],
    max_turns: int,
) -> WorkerResult:
    """Run a single agent with the given configuration."""
    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    prompt = _build_agent_system_prompt(agent, dag, project_context, prior_outputs)
    parent = get_session()
    nested = _build_nested(agent.role, prompt, parent.depth)
    nested.system_prompt = prompt
    if not nested.messages:
        nested.messages = [{"role": "system", "content": prompt}]
    nested.messages.append({"role": "user", "content": f"Begin your work: {agent.mission}"})

    token = push_session(nested)
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        content = getattr(result, "content", "") or ""
        turns = getattr(result, "turns_used", 0)
        return WorkerResult(
            agent_id=agent.id,
            role=agent.role,
            success=True,
            output=content,
            turns_used=turns,
        )
    except Exception as exc:
        return WorkerResult(
            agent_id=agent.id,
            role=agent.role,
            success=False,
            output="",
            error=str(exc),
        )
    finally:
        pop_session(token)


def _run_integrator(
    prior_outputs: Dict[str, str],
    dag: TaskDAG,
    project_context: str,
    user_input: str,
    max_turns: int,
) -> WorkerResult:
    """Run the final integrator agent."""
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
        return WorkerResult(
            agent_id="integrator",
            role="Integrator",
            success=True,
            output=content,
        )
    except Exception as exc:
        return WorkerResult(
            agent_id="integrator",
            role="Integrator",
            success=False,
            output="",
            error=str(exc),
        )
    finally:
        pop_session(token)


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
    """Execute the team according to the DAG and strategy."""
    from agent import ui

    agent_map: Dict[str, AgentRole] = {a.id: a for a in team.agents}
    completed_ids: Set[str] = set()
    outputs: Dict[str, WorkerResult] = {}
    prior_outputs: Dict[str, str] = {}
    failed: List[str] = []

    def _notify(aid: str, role: str, status: str) -> None:
        ui.muted(f"  [{status}] {role}")
        if on_progress:
            on_progress(aid, role, status)

    if strategy == "single" or len(team.agents) == 1:
        agent = team.agents[0]
        _notify(agent.id, agent.role, "running")
        result = _run_single_agent(agent, dag, project_context, {}, max_turns_per_agent)
        outputs[agent.id] = result
        if not result.success:
            failed.append(agent.id)
        _notify(agent.id, agent.role, "done" if result.success else "failed")
        return SchedulerResult(
            success=not failed,
            outputs=outputs,
            final_output=result.output,
            total_turns=result.turns_used,
            failed_agents=failed,
        )

    # Layer-by-layer execution
    layers = dag.layers()
    for layer in layers:
        # Find which agents own the nodes in this layer
        layer_agents: List[AgentRole] = []
        for node in layer:
            # Find agent owning this node
            for agent in team.agents:
                if node.id in agent.task_node_ids and agent.id not in completed_ids:
                    if agent not in layer_agents:
                        layer_agents.append(agent)

        if not layer_agents:
            continue

        if strategy == "parallel" and len(layer_agents) > 1:
            workers = min(max_workers, len(layer_agents))
            ctx = contextvars.copy_context()

            def _worker(ag: AgentRole) -> WorkerResult:
                return ctx.run(
                    _run_single_agent, ag, dag, project_context,
                    prior_outputs, max_turns_per_agent
                )

            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="kryth-agent"
            ) as pool:
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
                            success=False, output="", error=str(exc)
                        )
                    outputs[ag.id] = result
                    completed_ids.add(ag.id)
                    prior_outputs[ag.id] = result.output
                    if not result.success:
                        failed.append(ag.id)
                    _notify(ag.id, ag.role, "done" if result.success else "failed")

        else:
            # Sequential
            for agent in layer_agents:
                _notify(agent.id, agent.role, "running")
                result = _run_single_agent(
                    agent, dag, project_context, prior_outputs, max_turns_per_agent
                )
                outputs[agent.id] = result
                completed_ids.add(agent.id)
                prior_outputs[agent.id] = result.output
                if not result.success:
                    failed.append(agent.id)
                _notify(agent.id, agent.role, "done" if result.success else "failed")

    # Integrate results
    int_result = _run_integrator(
        prior_outputs, dag, project_context, user_input, max_turns_per_agent
    )
    outputs["integrator"] = int_result

    total_turns = sum(r.turns_used for r in outputs.values())
    final = int_result.output or next(
        (r.output for r in outputs.values() if r.output), ""
    )

    return SchedulerResult(
        success=not failed,
        outputs=outputs,
        final_output=final,
        total_turns=total_turns,
        failed_agents=failed,
    )
