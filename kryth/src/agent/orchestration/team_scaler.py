"""Team Scaler — explodes a single AgentRole into N parallel same-domain agents.

When a domain has enough work (files, estimated turns), scale_team() replaces
the single agent with N sub-agents, each owning an exclusive slice of the
directory tree. The scheduler then runs them in parallel within the same layer.

Work stealing (via WorkQueue) handles load balancing at runtime — if one
sub-agent finishes early, it picks up pending work from its slower sibling.

Usage:
    from agent.orchestration.team_scaler import scale_team
    scaled = scale_team(team, dag, project_root, user_input)
"""
from __future__ import annotations

import copy
from dataclasses import replace
from typing import List

from agent.orchestration.task_dag import TaskDAG
from agent.orchestration.team_generator import AgentRole, OwnedScope, TeamPlan
from agent.orchestration.work_partitioner import DomainChunk, partition_domain

# Minimum estimated_turns for an agent to be eligible for splitting.
# Below this, the coordination overhead outweighs the speedup.
_MIN_TURNS_TO_SPLIT = 50


def scale_team(
    team: TeamPlan,
    dag: TaskDAG,
    repo_root: str = ".",
    user_input: str = "",
    max_agents_per_domain: int = 10,
) -> TeamPlan:
    """Scale each AgentRole into N parallel sub-agents where warranted.

    Agents with few turns or few files keep their single-agent form.
    Agents with large workloads are exploded into N sub-agents with
    non-overlapping directory ownership.

    Returns a new TeamPlan with the scaled agent list and updated strategy.
    """
    new_agents: List[AgentRole] = []
    did_scale = False

    for agent in team.agents:
        if agent.max_turns < _MIN_TURNS_TO_SPLIT:
            new_agents.append(agent)
            continue

        chunks = partition_domain(
            owned_dirs=agent.owns.directories,
            repo_root=repo_root,
            role=agent.role,
            estimated_turns=agent.max_turns,
            max_agents=max_agents_per_domain,
            user_input=user_input,
        )

        if len(chunks) <= 1:
            new_agents.append(agent)
        else:
            sub_agents = _explode_agent(agent, chunks, dag)
            new_agents.extend(sub_agents)
            did_scale = True

    if not did_scale:
        return team

    # Recompute strategy — scaling always implies parallel is beneficial
    total_turns = sum(a.max_turns for a in new_agents)
    new_strategy = "parallel" if len(new_agents) > 1 else "single"
    reasoning = (
        f"Scaled to {len(new_agents)} agents "
        f"({sum(1 for a in new_agents if '#' in a.role)} sub-agents from domain splitting)"
    )

    return TeamPlan(
        agents=new_agents,
        complexity=team.complexity,
        risk_assessment=team.risk_assessment,
        estimated_total_turns=total_turns,
        estimated_total_tokens=total_turns * 2000,
        parallel_benefit=team.parallel_benefit,
        parallel_cost=team.parallel_cost,
        recommended_strategy=new_strategy,
        reasoning=reasoning,
        layer_count=team.layer_count,
    )


def _explode_agent(
    agent: AgentRole,
    chunks: List[DomainChunk],
    dag: TaskDAG,
) -> List[AgentRole]:
    """Create N sub-agents from a single agent, each owning one chunk.

    All sub-agents:
    - Share the same upstream dependencies (they can all start together)
    - Own non-overlapping directories
    - Get proportionally reduced max_turns
    - Are named "Role #1", "Role #2", etc.
    """
    n = len(chunks)
    sub_turns = max(20, agent.max_turns // n + 10)  # each does 1/N + buffer
    sub_agents: List[AgentRole] = []

    for chunk in chunks:
        aid = f"{agent.id}_{chunk.index}"
        role_name = f"{agent.role} #{chunk.index + 1}"

        # Build ownership from chunk dirs
        owned = OwnedScope(
            directories=list(chunk.dirs),
            files=list(chunk.files),
            symbols=[],
            ports=[],
        )

        # Specialise the mission for this chunk
        if chunk.dirs:
            dir_hint = ", ".join(chunk.dirs[:2])
            if len(chunk.dirs) > 2:
                dir_hint += f" (+{len(chunk.dirs)-2} more)"
            mission = f"{agent.mission} — focus on: {dir_hint}"
        elif chunk.description:
            mission = f"{agent.mission} — {chunk.description}"
        else:
            mission = agent.mission

        sub = AgentRole(
            id=aid,
            role=role_name,
            mission=mission,
            owns=owned,
            task_node_ids=list(agent.task_node_ids),
            dependencies=list(agent.dependencies),  # same upstream deps
            validation_rules=list(agent.validation_rules),
            recovery_rules=list(agent.recovery_rules),
            tools=list(agent.tools),
            max_turns=sub_turns,
        )
        sub_agents.append(sub)

    # Inject a Lead agent when there are 3+ workers — it coordinates them
    if len(sub_agents) >= 3:
        sub_agents = _inject_lead_agent(sub_agents, agent)

    return sub_agents


def _inject_lead_agent(workers: List[AgentRole], parent: AgentRole) -> List[AgentRole]:
    """Prepend a Lead agent that coordinates N workers.

    The Lead runs first (no dependencies on workers), produces a
    coordination plan with exact file assignments, then workers run using
    the Lead's plan as prior context. Workers gain a dependency on the lead.
    """
    domain = parent.id.split("_")[0]
    lead_id = f"{domain}_lead"
    lead = AgentRole(
        id=lead_id,
        role=f"{parent.role.split(' #')[0]} Lead",
        mission=(
            f"You are the LEAD coordinating {len(workers)} parallel {parent.role.split(' #')[0]} workers. "
            f"Your job: read the codebase, then produce a DETAILED COORDINATION PLAN with exact file "
            f"assignments for each worker. Start with LEAD_PLAN: and list each worker's exact files. "
            f"Do NOT write code yourself — only plan."
        ),
        owns=OwnedScope(),
        task_node_ids=list(parent.task_node_ids[:1]),  # owns first node for DAG placement
        dependencies=list(parent.dependencies),         # same upstream as workers
        validation_rules=[],
        recovery_rules=[],
        tools=["search_smart", "grep", "glob", "read_file"],
        max_turns=20,
    )
    # All workers depend on the lead
    for w in workers:
        if lead_id not in w.dependencies:
            w.dependencies.insert(0, lead_id)

    return [lead] + workers
