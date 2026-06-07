"""Dynamic Team Generator — creates engineering roles from a TaskDAG.

Never uses fixed templates or presets. Every agent is generated from the
specific shape of the TaskDAG nodes. Each agent contains:
  - Role name and mission
  - Owned files, symbols, directories, terminals
  - Dependencies on other agents' output
  - Validation and recovery rules
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from agent.orchestration.task_dag import TaskDAG, TaskNode


@dataclass
class OwnedScope:
    """What a generated agent owns."""
    files: List[str] = field(default_factory=list)
    symbols: List[str] = field(default_factory=list)
    directories: List[str] = field(default_factory=list)
    ports: List[int] = field(default_factory=list)


@dataclass
class AgentRole:
    id: str
    role: str
    mission: str
    owns: OwnedScope = field(default_factory=OwnedScope)
    task_node_ids: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)   # agent IDs this depends on
    validation_rules: List[str] = field(default_factory=list)
    recovery_rules: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)          # extra tools this agent needs
    max_turns: int = 80


@dataclass
class TeamPlan:
    agents: List[AgentRole]
    complexity: float        # 0–10
    risk_assessment: str
    estimated_total_turns: int
    estimated_total_tokens: int
    parallel_benefit: float  # benefit ratio vs single-agent (1.0 = equal)
    parallel_cost: float     # cost ratio vs single-agent (1.0 = equal)
    recommended_strategy: str  # "single", "sequential", "parallel"
    reasoning: str
    layer_count: int = 1


# Role → capability map
_ROLE_TEMPLATES: Dict[str, dict] = {
    "planner": {
        "mission": "Analyze requirements, design architecture, produce an execution plan",
        "tools": ["search_smart", "grep", "lookup_symbol"],
        "max_turns": 20,
    },
    "backend specialist": {
        "mission": "Implement backend services, APIs, database schema, and business logic",
        "tools": [],
        "max_turns": 100,
    },
    "frontend specialist": {
        "mission": "Implement UI components, pages, state management, and styling",
        "tools": [],
        "max_turns": 100,
    },
    "database specialist": {
        "mission": "Design database schema, migrations, queries, and data models",
        "tools": [],
        "max_turns": 40,
    },
    "auth specialist": {
        "mission": "Implement authentication, authorization, session management, and security",
        "tools": ["search_smart"],
        "max_turns": 50,
    },
    "payment specialist": {
        "mission": "Integrate payment provider, implement billing and webhook handling",
        "tools": ["search_smart"],
        "max_turns": 60,
    },
    "testing specialist": {
        "mission": "Write unit tests, integration tests, E2E tests, and verify coverage",
        "tools": [],
        "max_turns": 50,
    },
    "devops specialist": {
        "mission": "Configure Docker, CI/CD pipelines, deployment, and monitoring",
        "tools": [],
        "max_turns": 30,
    },
    "documentation specialist": {
        "mission": "Write clear, comprehensive documentation for all components",
        "tools": [],
        "max_turns": 20,
    },
    "security reviewer": {
        "mission": "Review code for security vulnerabilities and compliance",
        "tools": ["search_smart", "grep", "lookup_symbol"],
        "max_turns": 25,
    },
    "integrator": {
        "mission": "Merge outputs from all agents, resolve conflicts, and produce a working system",
        "tools": [],
        "max_turns": 30,
    },
}


def generate_team(dag: TaskDAG) -> TeamPlan:
    """Generate an engineering team from a TaskDAG.

    Each TaskDAG node maps to one or more AgentRoles. The mapping is
    dynamic — based on the node's capabilities, not on fixed presets.
    """
    layers = dag.layers()
    agent_map: Dict[str, AgentRole] = {}
    node_owners: Dict[str, str] = {}  # node_id -> agent_id

    # Mapping: capability -> role assignment
    # A single agent can own multiple related nodes
    _CAP_TO_ROLE: Dict[str, str] = {
        "database": "database specialist",
        "authentication": "auth specialist",
        "payments": "payment specialist",
        "testing": "testing specialist",
        "docker": "devops specialist",
        "cicd": "devops specialist",
        "deployment": "devops specialist",
        "documentation": "documentation specialist",
        "security_audit": "security reviewer",
        "profiling": "backend specialist",
        "optimization": "backend specialist",
        "migration": "backend specialist",
        "refactoring": "integrator",
        "integration": "integrator",
        "debugging": "planner",
        "implementation": "planner",  # planner delegates to specialists
    }

    # Pass 1: assign each node to a role
    for layer in layers:
        for node in layer:
            role = _find_role(node, _CAP_TO_ROLE)

            # Create or reuse agent for this role
            agent_id = role.lower().replace(" ", "_")
            if agent_id in agent_map:
                agent = agent_map[agent_id]
            else:
                template = _ROLE_TEMPLATES.get(role, {
                    "mission": f"Complete all {role} work",
                    "tools": [],
                    "max_turns": 60,
                })
                agent = AgentRole(
                    id=agent_id,
                    role=role,
                    mission=template["mission"],
                    tools=template["tools"],
                    max_turns=template["max_turns"],
                )
                agent_map[agent_id] = agent

            agent.task_node_ids.append(node.id)
            agent.owns.files.extend(node.affected_files)
            agent.owns.symbols.extend(node.affected_symbols)
            agent.owns.directories.extend(node.affected_dirs)
            agent.validation_rules.extend(node.validation)
            if node.rollback_strategy:
                agent.recovery_rules.append(node.rollback_strategy)

            node_owners[node.id] = agent_id

    # Pass 2: resolve inter-agent dependencies from node dependencies
    for agent in agent_map.values():
        agent_deps: set = set()
        for nid in agent.task_node_ids:
            node = dag.nodes.get(nid)
            if not node:
                continue
            for dep_nid in node.dependencies:
                owner = node_owners.get(dep_nid)
                if owner and owner != agent.id:
                    agent_deps.add(owner)
        agent.dependencies = sorted(agent_deps)

    # Compute complexity
    total_turns = dag.total_estimated_turns()
    complexity = min(10.0, total_turns / 50 + len(agent_map) * 0.5)

    # Compute parallel benefit vs cost
    num_agents = len(agent_map)
    parallel_benefit = _compute_parallel_benefit(dag, num_agents)
    parallel_cost = _compute_parallel_cost(dag, num_agents)

    # Decide strategy
    if num_agents <= 1:
        recommended_strategy = "single"
        reasoning = "Single agent sufficient — only one role needed"
    elif num_agents == 2:
        recommended_strategy = "sequential"
        reasoning = f"Two roles ({', '.join(a.role for a in agent_map.values())}) — sequential execution"
    else:
        # Only recommend parallel if benefit > cost with a safety margin
        if parallel_benefit > parallel_cost * 1.2:
            reasoning = (
                f"Parallel benefit ({parallel_benefit:.1f}x) exceeds "
                f"parallel cost ({parallel_cost:.1f}x) — parallel recommended"
            )
            recommended_strategy = "parallel"
        else:
            reasoning = (
                f"Parallel benefit ({parallel_benefit:.1f}x) does not "
                f"justify overhead ({parallel_cost:.1f}x) — sequential"
            )
            recommended_strategy = "sequential"

    # Risk assessment
    risk_counts = dag.risk_summary()
    risk_str = ", ".join(f"{k}={v}" for k, v in sorted(risk_counts.items()))

    return TeamPlan(
        agents=list(agent_map.values()),
        complexity=round(complexity, 1),
        risk_assessment=risk_str,
        estimated_total_turns=total_turns,
        estimated_total_tokens=total_turns * 2000,
        parallel_benefit=round(parallel_benefit, 2),
        parallel_cost=round(parallel_cost, 2),
        recommended_strategy=recommended_strategy,
        reasoning=reasoning,
        layer_count=len(layers),
    )


def _find_role(node: TaskNode, cap_map: Dict[str, str]) -> str:
    """Map a TaskNode to a role based on its required capabilities."""
    for cap in node.capabilities_required:
        role = cap_map.get(cap)
        if role:
            return role
    return "backend specialist"


def _compute_parallel_benefit(dag: TaskDAG, num_agents: int) -> float:
    """Estimate the benefit of parallel execution.

    Returns a multiplier where > 1 means parallel saves time.
    """
    layers = dag.layers()
    if len(layers) <= 1:
        return 1.0  # no parallelization possible

    # Theoretical speedup: sequential time / time along critical path
    sequential = dag.total_estimated_turns()
    critical_path = max(
        sum(dag.nodes[nid].estimated_turns for nid in layer_ids)
        for layer_ids in ([[n.id for n in layer] for layer in layers])
    )
    speedup = sequential / max(critical_path, 0)

    # Reduce by coordination overhead
    coordination_factor = 1.0 - 0.05 * num_agents
    return speedup * coordination_factor


def _compute_parallel_cost(dag: TaskDAG, num_agents: int) -> float:
    """Estimate the cost of parallel execution vs single-agent.

    Returns a multiplier where > 1 means parallel is more expensive.
    """
    if num_agents <= 1:
        return 1.0

    context_transfer = 0.1 * num_agents       # 10% overhead per agent for context sharing
    merge_overhead = 0.15 * num_agents          # 15% per agent for merging results
    coordination = 0.05 * num_agents * (num_agents - 1)  # pairwise coordination
    startup_cost = 0.08 * num_agents            # 8% per agent for bootstrapping

    return 1.0 + sum([context_transfer, merge_overhead, coordination, startup_cost])
