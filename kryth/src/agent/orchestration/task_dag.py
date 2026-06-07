"""Task DAG — dependency graph of work items derived from capability graph.

Each node contains the task, dependencies, risk level, estimated cost,
affected files/symbols/directories, validation rules, and rollback strategy.

This is the blueprint that team_generator uses to create agents.
"""
from __future__ import annotations

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
    capabilities_required: List[str]          # which Capability names
    dependencies: List[str] = field(default_factory=list)  # node IDs this depends on
    risk: RiskLevel = RiskLevel.LOW
    estimated_turns: int = 30
    affected_dirs: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)
    affected_symbols: List[str] = field(default_factory=list)
    validation: List[str] = field(default_factory=list)
    rollback_strategy: str = ""
    is_blocking: bool = False                  # if True, all other work waits on this


@dataclass
class TaskDAG:
    name: str
    nodes: Dict[str, TaskNode] = field(default_factory=dict)  # id -> node
    dependencies: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, node: TaskNode) -> None:
        self.nodes[node.id] = node
        self.dependencies[node.id] = node.dependencies

    def get(self, node_id: str) -> Optional[TaskNode]:
        return self.nodes.get(node_id)

    def ready_nodes(self, completed: Set[str]) -> List[TaskNode]:
        """Return nodes whose dependencies are all satisfied."""
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
        """Topological layers: each layer can be executed in parallel."""
        completed: Set[str] = set()
        layers: List[List[TaskNode]] = []
        remaining = set(self.nodes.keys())

        while remaining:
            layer = []
            for nid in list(remaining):
                node = self.nodes[nid]
                if all(dep in completed for dep in node.dependencies):
                    layer.append(node)
                    completed.add(nid)
                    remaining.discard(nid)
            if not layer:
                # Circular dependency — break it by taking the first remaining
                if remaining:
                    nid = next(iter(remaining))
                    layer.append(self.nodes[nid])
                    completed.add(nid)
                    remaining.discard(nid)
            layers.append(layer)

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
# Builder
# ---------------------------------------------------------------------------

def build_task_dag(
    name: str,
    capabilities_required: List[str],
    repo_root: str = ".",
    user_input: str = "",
) -> TaskDAG:
    """Build a TaskDAG from a list of required capabilities.

    This maps each capability to one or more concrete task nodes. The
    resulting DAG encodes dependency ordering so the scheduler knows
    which nodes can run in parallel and which must wait.
    """
    dag = TaskDAG(name=name)
    text = user_input.lower()

    # --- Phase 1: Infrastructure / foundational nodes (no dependencies) ---

    if "database" in capabilities_required:
        dag.add(TaskNode(
            id="database",
            name="Database Schema & Setup",
            description="Design and implement database schema, models, and migrations",
            capabilities_required=["database"],
            risk=RiskLevel.LOW,
            estimated_turns=25,
            validation=["Schema design review", "Migration tests pass"],
            rollback_strategy="Roll back migration, restore previous schema",
        ))

    if "docker" in capabilities_required:
        dag.add(TaskNode(
            id="docker",
            name="Docker Configuration",
            description="Create Dockerfiles and docker-compose for local dev and deployment",
            capabilities_required=["docker"],
            dependencies=["database"] if "database" in capabilities_required else [],
            risk=RiskLevel.LOW,
            estimated_turns=15,
            validation=["docker compose up succeeds", "Health check passes"],
        ))

    if "authentication" in capabilities_required:
        auth_deps = []
        if "database" in capabilities_required:
            auth_deps.append("database")
        dag.add(TaskNode(
            id="authentication",
            name="Authentication & Authorization",
            description="Implement auth: login, signup, session management, role-based access",
            capabilities_required=["authentication"],
            dependencies=auth_deps,
            risk=RiskLevel.HIGH,
            estimated_turns=40,
            validation=["Auth flow E2E tests pass", "Security review"],
            rollback_strategy="Feature flag the auth module; roll back to existing auth",
        ))

    if "payments" in capabilities_required:
        pay_deps = []
        if "database" in capabilities_required:
            pay_deps.append("database")
        if "authentication" in capabilities_required:
            pay_deps.append("authentication")
        dag.add(TaskNode(
            id="payments",
            name="Payment Integration",
            description="Implement Stripe checkout, billing portal, webhook handling",
            capabilities_required=["payments"],
            dependencies=pay_deps,
            risk=RiskLevel.HIGH,
            estimated_turns=50,
            validation=["Checkout flow works end-to-end", "Webhook signature verified",
                        "Test card succeeds/fails correctly"],
            rollback_strategy="Feature flag payments; API version pin for rollback",
        ))

    # --- Phase 2: Backend / API (depends on database and auth) ---

    if "backend" in capabilities_required:
        be_deps = [d for d in ["database", "authentication", "payments"]
                   if d in dag.nodes]
        dag.add(TaskNode(
            id="backend",
            name="Backend API",
            description="Build backend API with routes, controllers, middleware",
            capabilities_required=["backend"],
            dependencies=be_deps,
            risk=RiskLevel.MEDIUM,
            estimated_turns=60,
            validation=["API endpoint tests pass", "Integration tests pass"],
            rollback_strategy="Versioned API; deploy new version, keep old active",
        ))

    # --- Phase 3: Frontend (depends on backend/api) ---

    if "frontend" in capabilities_required:
        fe_deps = [d for d in ["backend", "authentication"]
                   if d in dag.nodes]
        dag.add(TaskNode(
            id="frontend",
            name="Frontend UI",
            description="Build the frontend with pages, components, state management",
            capabilities_required=["frontend"],
            dependencies=fe_deps,
            risk=RiskLevel.MEDIUM,
            estimated_turns=80,
            validation=["UI smoke tests pass", "Responsive design check"],
        ))

    # --- Phase 4: Cross-cutting nodes ---

    if "testing" in capabilities_required:
        test_deps = [nid for nid in dag.nodes if nid not in ("testing", "documentation", "cicd")]
        dag.add(TaskNode(
            id="testing",
            name="Testing & Coverage",
            description="Write unit, integration, and E2E tests",
            capabilities_required=["testing"],
            dependencies=test_deps,
            risk=RiskLevel.LOW,
            estimated_turns=35,
            validation=["Coverage meets threshold", "All tests pass"],
        ))

    if "cicd" in capabilities_required:
        cicd_deps = [nid for nid in dag.nodes if nid not in ("cicd", "documentation")]
        dag.add(TaskNode(
            id="cicd",
            name="CI/CD Pipeline",
            description="Set up CI/CD with testing and deployment steps",
            capabilities_required=["cicd"],
            dependencies=cicd_deps,
            risk=RiskLevel.MEDIUM,
            estimated_turns=20,
            validation=["Pipeline runs end-to-end in CI"],
        ))

    if "documentation" in capabilities_required or "documentation" in [c.lower() for c in capabilities_required]:
        doc_deps = [nid for nid in dag.nodes
                    if nid not in ("documentation", "cicd")]
        dag.add(TaskNode(
            id="documentation",
            name="Documentation",
            description="Write README, API docs, and setup guide",
            capabilities_required=["documentation"],
            dependencies=doc_deps,
            risk=RiskLevel.LOW,
            estimated_turns=15,
            validation=["All public APIs documented", "Setup guide verified"],
        ))

    # If nothing was added, create a single root node
    if not dag.nodes:
        dag.add(TaskNode(
            id="main",
            name="Main Task",
            description="Complete the requested work",
            capabilities_required=capabilities_required,
            risk=RiskLevel.MEDIUM,
            estimated_turns=100,
        ))

    return dag
