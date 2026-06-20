"""Task DAG — dependency graph of work items derived from capability graph.

Each node contains the task, dependencies, risk level, estimated cost,
affected files/symbols/directories, validation rules, and rollback strategy.

This is the blueprint that team_generator uses to create agents.

DAG nodes are generated dynamically at runtime using an LLM that analyses
the user_input + repo_profile + required_capabilities. The fixed-type
fallback (_build_fixed_dag) is used when the LLM is unavailable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class TaskNode:
    id: str
    name: str
    description: str
    capabilities_required: List[str]
    dependencies: List[str] = field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW
    estimated_turns: int = 30
    affected_dirs: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    affected_symbols: List[str] = field(default_factory=list)
    validation: List[str] = field(default_factory=list)
    rollback_strategy: str = ""
    is_blocking: bool = False


@dataclass
class TaskDAG:
    name: str
    nodes: Dict[str, TaskNode] = field(default_factory=dict)
    dependencies: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, node: TaskNode) -> None:
        self.nodes[node.id] = node
        self.dependencies[node.id] = node.dependencies

    def get(self, node_id: str) -> Optional[TaskNode]:
        return self.nodes.get(node_id)

    def ready_nodes(self, completed: Set[str]) -> List[TaskNode]:
        result = []
        for nid, node in self.nodes.items():
            if nid in completed:
                continue
            if all(dep in completed for dep in node.dependencies):
                result.append(node)
        return result

    def total_estimated_turns(self) -> int:
        return sum(n.estimated_turns for n in self.nodes.values())

    def layers(self) -> List[List[TaskNode]]:
        """Topological layers: each layer can be executed in parallel.

        A node belongs to layer N iff ALL of its dependencies completed in a
        STRICTLY EARLIER layer (< N). The set of completed nodes is therefore
        snapshotted per layer: nodes placed in the current layer must NOT
        satisfy dependencies for their siblings in that same layer — otherwise
        a dependent node could be wrongly merged into its dependency's layer,
        breaking parallel-scheduling correctness.

        This mirrors the agent-level scheduler (`_agent_execution_layers`),
        which is the runtime source of truth.

        Iteration follows insertion order so layer contents are deterministic
        regardless of hash seed.
        """
        completed: Set[str] = set()
        layers: List[List[TaskNode]] = []
        remaining = set(self.nodes.keys())
        while remaining:
            # Ready = every dependency was completed in a PRIOR layer.
            # `completed` is read-only for the duration of this pass; the new
            # layer is folded in only AFTER the layer is fully built.
            ready = [
                nid
                for nid in self.nodes            # insertion order → deterministic
                if nid in remaining
                and all(dep in completed for dep in self.nodes[nid].dependencies)
            ]
            if not ready:
                # No progress possible: a dependency points at a missing node
                # or a cycle exists. Emit one node to guarantee termination
                # (fail-safe behavior preserved from the original).
                nid = next(n for n in self.nodes if n in remaining)
                ready = [nid]
            layers.append([self.nodes[nid] for nid in ready])
            for nid in ready:
                completed.add(nid)
                remaining.discard(nid)
        return layers

    def risk_summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.risk.value] = counts.get(node.risk.value, 0) + 1
        return counts

    def serialize(self) -> dict:
        return {
            "name": self.name,
            "nodes": [
                {
                    "id": n.id,
                    "name": n.name,
                    "description": n.description,
                    "capabilities_required": n.capabilities_required,
                    "dependencies": n.dependencies,
                    "risk": n.risk.value,
                    "turns": n.estimated_turns,
                    "affected_dirs": n.affected_dirs,
                    "validation": n.validation,
                }
                for n in self.nodes.values()
            ],
        }


# ---------------------------------------------------------------------------
# LLM-generated dynamic DAG
# ---------------------------------------------------------------------------

_DAG_SYSTEM = """You are a software engineering task planner.
Given a user request, the repo profile, and required capabilities, generate a
task DAG (directed acyclic graph) where each node is a concrete engineering task.

Rules:
- Generate 2-10 nodes. Never more than 12.
- Each node must have a unique kebab-case id (e.g. "api-layer", "auth-module").
- depends_on lists IDs of nodes that must complete before this one starts.
- risk: "low" | "medium" | "high" | "critical"
- estimated_turns: integer 15-120 (how many LLM turns this task needs)
- affected_dirs: list of directories this task will modify (e.g. ["src/api", "src/auth"])
- validation: list of verification steps

Return ONLY a valid JSON array - no prose, no markdown fences.

Example:
[
  {"id":"db-schema","name":"Database Schema","description":"Design tables, relations, migrations","capabilities_required":["database"],"depends_on":[],"risk":"low","estimated_turns":25,"affected_dirs":["src/db"],"validation":["migrations run clean"]},
  {"id":"api-layer","name":"REST API","description":"Build CRUD endpoints","capabilities_required":["backend"],"depends_on":["db-schema"],"risk":"medium","estimated_turns":50,"affected_dirs":["src/api"],"validation":["all routes return 200"]}
]"""


def _llm_build_dag(
    user_input: str,
    capabilities: List[str],
    repo_summary: str,
) -> Optional[List[dict]]:
    """Ask the planner model to generate a dynamic DAG. Returns raw node dicts or None."""
    try:
        from agent.llm import _get_client, PLANNER_MODEL

        prompt = (
            f"User request: {user_input[:600]}\n\n"
            f"Required capabilities: {', '.join(capabilities)}\n\n"
            f"Repository: {repo_summary[:800]}\n\n"
            "Generate the task DAG:"
        )
        try:
            from agent.model_router import TaskRole, pick_model_for_role
            _dag_model = pick_model_for_role(TaskRole.PLANNING)
        except Exception:
            _dag_model = PLANNER_MODEL
        client = _get_client()
        resp = client.chat.completions.create(
            model=_dag_model,
            messages=[
                {"role": "system", "content": _DAG_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=1200,
            timeout=7,
        )
        raw = (resp.choices[0].message.content or "").strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            return None
        nodes = json.loads(raw[start:end + 1])
        if isinstance(nodes, list) and nodes:
            return nodes
    except Exception:
        pass
    return None


def _parse_llm_nodes(raw_nodes: List[dict], capabilities: List[str]) -> Optional[TaskDAG]:
    """Validate and convert raw LLM node dicts into a TaskDAG with cycle check."""
    seen: set = set()
    nodes: list[TaskNode] = []
    for raw in raw_nodes[:12]:
        nid = str(raw.get("id", "")).strip().replace(" ", "-")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        risk_str = str(raw.get("risk", "medium")).lower()
        risk = RiskLevel(risk_str) if risk_str in ("low", "medium", "high", "critical") else RiskLevel.MEDIUM
        deps = [str(d).strip() for d in (raw.get("depends_on") or []) if str(d).strip()]
        try:
            turns = max(10, min(150, int(raw.get("estimated_turns", 40))))
        except (TypeError, ValueError):
            turns = 40
        nodes.append(TaskNode(
            id=nid,
            name=str(raw.get("name", nid)),
            description=str(raw.get("description", "")),
            capabilities_required=list(raw.get("capabilities_required", capabilities[:1])),
            dependencies=deps,
            risk=risk,
            estimated_turns=turns,
            affected_dirs=list(raw.get("affected_dirs") or []),
            validation=list(raw.get("validation") or []),
        ))

    if not nodes:
        return None

    ids = {n.id for n in nodes}
    for n in nodes:
        n.dependencies = [d for d in n.dependencies if d in ids]

    # Kahn's topo-sort + cycle detection
    indegree = {n.id: len(n.dependencies) for n in nodes}
    order: list[str] = []
    queue = [n.id for n in nodes if indegree[n.id] == 0]
    id_to_node = {n.id: n for n in nodes}
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for other in nodes:
            if nid in other.dependencies:
                indegree[other.id] -= 1
                if indegree[other.id] == 0:
                    queue.append(other.id)

    if len(order) != len(nodes):
        return None  # cycle — fall back to fixed DAG

    dag = TaskDAG(name="dynamic")
    for nid in order:
        dag.add(id_to_node[nid])
    return dag


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_task_dag(
    name: str,
    capabilities_required: List[str],
    repo_root: str = ".",
    user_input: str = "",
    repo_profile=None,
) -> TaskDAG:
    """Build a TaskDAG dynamically.

    Tries LLM-generated DAG first (specific to the exact request).
    Falls back to the fixed-type DAG if LLM is unavailable or times out.
    """
    repo_summary = ""
    if repo_profile is not None:
        try:
            parts = []
            if getattr(repo_profile, "languages", []):
                parts.append(f"Languages: {', '.join(repo_profile.languages[:3])}")
            if getattr(repo_profile, "frameworks", []):
                parts.append(f"Frameworks: {', '.join(repo_profile.frameworks[:4])}")
            if getattr(repo_profile, "architecture", []):
                parts.append(f"Architecture: {', '.join(repo_profile.architecture[:3])}")
            repo_summary = "; ".join(parts)
        except Exception:
            pass

    if user_input and capabilities_required:
        raw = _llm_build_dag(user_input, capabilities_required, repo_summary)
        if raw:
            dag = _parse_llm_nodes(raw, capabilities_required)
            if dag and dag.nodes:
                dag.name = name
                return dag

    return _build_fixed_dag(name, capabilities_required)


# ---------------------------------------------------------------------------
# Fixed-type fallback DAG (original implementation, preserved)
# ---------------------------------------------------------------------------

def _build_fixed_dag(name: str, capabilities_required: List[str]) -> TaskDAG:
    """8-node fixed DAG used as fallback when LLM is unavailable."""
    dag = TaskDAG(name=name)

    if "database" in capabilities_required:
        dag.add(TaskNode(
            id="database", name="Database Schema & Setup",
            description="Design and implement database schema, models, and migrations",
            capabilities_required=["database"], risk=RiskLevel.LOW, estimated_turns=25,
            validation=["Schema design review", "Migration tests pass"],
            rollback_strategy="Roll back migration, restore previous schema",
        ))

    if "docker" in capabilities_required:
        dag.add(TaskNode(
            id="docker", name="Docker Configuration",
            description="Create Dockerfiles and docker-compose for local dev and deployment",
            capabilities_required=["docker"],
            dependencies=["database"] if "database" in capabilities_required else [],
            risk=RiskLevel.LOW, estimated_turns=15,
            validation=["docker compose up succeeds", "Health check passes"],
        ))

    if "authentication" in capabilities_required:
        dag.add(TaskNode(
            id="authentication", name="Authentication & Authorization",
            description="Implement auth: login, signup, session management, role-based access",
            capabilities_required=["authentication"],
            dependencies=["database"] if "database" in capabilities_required else [],
            risk=RiskLevel.HIGH, estimated_turns=40,
            validation=["Auth flow E2E tests pass", "Security review"],
            rollback_strategy="Feature flag the auth module; roll back to existing auth",
        ))

    if "payments" in capabilities_required:
        dag.add(TaskNode(
            id="payments", name="Payment Integration",
            description="Implement Stripe checkout, billing portal, webhook handling",
            capabilities_required=["payments"],
            dependencies=[d for d in ["database", "authentication"] if d in dag.nodes],
            risk=RiskLevel.HIGH, estimated_turns=50,
            validation=["Checkout flow works end-to-end", "Webhook signature verified"],
            rollback_strategy="Feature flag payments; API version pin for rollback",
        ))

    if "backend" in capabilities_required:
        dag.add(TaskNode(
            id="backend", name="Backend API",
            description="Build backend API with routes, controllers, middleware",
            capabilities_required=["backend"],
            dependencies=[d for d in ["database", "authentication", "payments"] if d in dag.nodes],
            risk=RiskLevel.MEDIUM, estimated_turns=60,
            validation=["API endpoint tests pass", "Integration tests pass"],
            rollback_strategy="Versioned API; deploy new version, keep old active",
        ))

    if "frontend" in capabilities_required:
        dag.add(TaskNode(
            id="frontend", name="Frontend UI",
            description="Build the frontend with pages, components, state management",
            capabilities_required=["frontend"],
            dependencies=[d for d in ["backend", "authentication"] if d in dag.nodes],
            risk=RiskLevel.MEDIUM, estimated_turns=80,
            validation=["UI smoke tests pass", "Responsive design check"],
        ))

    if "testing" in capabilities_required:
        dag.add(TaskNode(
            id="testing", name="Testing & Coverage",
            description="Write unit, integration, and E2E tests",
            capabilities_required=["testing"],
            dependencies=[nid for nid in dag.nodes if nid not in ("testing", "documentation", "cicd")],
            risk=RiskLevel.LOW, estimated_turns=35,
            validation=["Coverage meets threshold", "All tests pass"],
        ))

    if "cicd" in capabilities_required:
        dag.add(TaskNode(
            id="cicd", name="CI/CD Pipeline",
            description="Set up CI/CD with testing and deployment steps",
            capabilities_required=["cicd"],
            dependencies=[nid for nid in dag.nodes if nid not in ("cicd", "documentation")],
            risk=RiskLevel.MEDIUM, estimated_turns=20,
            validation=["Pipeline runs end-to-end in CI"],
        ))

    if "documentation" in capabilities_required:
        dag.add(TaskNode(
            id="documentation", name="Documentation",
            description="Write README, API docs, and setup guide",
            capabilities_required=["documentation"],
            dependencies=[nid for nid in dag.nodes if nid not in ("documentation", "cicd")],
            risk=RiskLevel.LOW, estimated_turns=15,
            validation=["All public APIs documented", "Setup guide verified"],
        ))

    if not dag.nodes:
        dag.add(TaskNode(
            id="main", name="Main Task",
            description="Complete the requested work",
            capabilities_required=capabilities_required,
            risk=RiskLevel.MEDIUM, estimated_turns=100,
        ))

    return dag
