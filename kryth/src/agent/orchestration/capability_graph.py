"""Capability Graph — derives required capabilities from intent + repo profile.

No agents are generated here. This module only answers:
"What capabilities does this task require?"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from agent.orchestration.intent_engine import IntentProfile
from agent.orchestration.repo_intelligence import RepoProfile


@dataclass
class Capability:
    name: str
    required: bool          # False = nice-to-have
    rationale: str
    affected_dirs: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)


@dataclass
class CapabilityGraph:
    capabilities: List[Capability]
    names: Set[str] = field(default_factory=set)

    def has(self, name: str) -> bool:
        return name in self.names

    def required_names(self) -> List[str]:
        return [c.name for c in self.capabilities if c.required]

    def all_names(self) -> List[str]:
        return [c.name for c in self.capabilities]


# ---------------------------------------------------------------------------
# Capability derivation rules
# ---------------------------------------------------------------------------

def build_capability_graph(
    intent: IntentProfile,
    repo: RepoProfile,
    user_input: str,
) -> CapabilityGraph:
    """Build the capability graph for a task."""
    caps: List[Capability] = []
    seen: Set[str] = set()

    def add(name: str, required: bool, rationale: str,
            dirs: Optional[List[str]] = None,
            files: Optional[List[str]] = None) -> None:
        if name not in seen:
            seen.add(name)
            caps.append(Capability(
                name=name,
                required=required,
                rationale=rationale,
                affected_dirs=dirs or [],
                affected_files=files or [],
            ))

    text = user_input.lower()
    intents = {i.type for i in intent.intents}
    primary = intent.primary

    # --- From intent ---

    if primary in ("bug_fix", "debugging"):
        add("debugging", True, "Primary task is a bug fix")
        add("testing", True, "Bug fixes must be verified with tests")

    if primary == "feature":
        add("implementation", True, "New feature requested")
        if intent.is_compound:
            add("integration", True, "Multiple features must be integrated")

    if primary == "refactor":
        add("refactoring", True, "Refactoring requested")
        add("testing", True, "Refactors need test coverage to avoid regression")

    if primary == "migration":
        add("migration", True, "Migration requested")
        add("testing", True, "Migration needs validation")
        add("rollback", False, "Rollback strategy advisable for migrations")

    if primary == "optimization":
        add("profiling", True, "Need to identify bottlenecks before optimizing")
        add("optimization", True, "Performance optimization required")
        add("benchmarking", False, "Benchmarks demonstrate improvement")

    if primary == "security":
        add("security_audit", True, "Security analysis required")
        add("testing", True, "Security patches need regression tests")

    if primary == "testing":
        add("testing", True, "Primary task is writing/fixing tests")

    if primary == "documentation":
        add("documentation", True, "Documentation requested")

    if primary in ("deployment", "infrastructure"):
        add("deployment", True, "Deployment configuration required")
        add("docker", False, "Container configuration may be needed")
        add("cicd", False, "CI/CD pipeline may be needed")

    # --- From user input text ---

    if any(k in text for k in ("frontend", "ui", "page", "component", "react", "next",
                                "vue", "svelte", "dashboard", "landing", "tailwind",
                                "vite", "css", "html", "npm", "tsx", "jsx")):
        add("frontend", True, "Frontend work detected in request")

    if any(k in text for k in ("backend", "api", "server", "endpoint", "route",
                                "service", "controller", "handler")):
        add("backend", True, "Backend work detected in request")

    if any(k in text for k in ("database", "db", "schema", "migration", "model",
                                "postgres", "mysql", "sqlite", "mongo")):
        add("database", True, "Database work detected in request")

    if any(k in text for k in ("auth", "login", "signup", "session", "jwt", "oauth",
                                "password", "permission", "role")):
        add("authentication", True, "Auth work detected in request")

    if any(k in text for k in ("stripe", "payment", "billing", "subscription",
                                "checkout", "webhook", "invoice")):
        add("payments", True, "Payment integration detected")

    if any(k in text for k in ("docker", "container", "compose", "image")):
        add("docker", True, "Docker work detected")

    if any(k in text for k in ("ci", "cd", "pipeline", "workflow", "action",
                                "deploy", "vercel", "netlify", "fly")):
        add("cicd", True, "CI/CD work detected")

    if any(k in text for k in ("test", "spec", "coverage", "unit", "integration", "e2e")):
        add("testing", True, "Testing work detected")

    if any(k in text for k in ("doc", "readme", "comment", "docstring")):
        add("documentation", False, "Documentation may be needed")

    # --- From repo state ---

    if repo.has_frontend and ("frontend" not in seen):
        add("frontend", False, "Existing frontend detected in repo")

    if repo.has_backend and ("backend" not in seen):
        add("backend", False, "Existing backend detected in repo")

    if repo.has_database and ("database" not in seen):
        add("database", False, "Existing database layer detected")

    if repo.has_auth and ("authentication" not in seen):
        add("authentication", False, "Existing auth detected in repo")

    if repo.has_payments and ("payments" not in seen):
        add("payments", False, "Existing payment integration detected")

    if repo.has_docker and ("docker" not in seen):
        add("docker", False, "Existing Docker setup detected")

    if repo.has_cicd and ("cicd" not in seen):
        add("cicd", False, "Existing CI/CD detected")

    # Testing is always at least nice-to-have
    if "testing" not in seen:
        add("testing", False, "Tests are always recommended")

    return CapabilityGraph(capabilities=caps, names=seen)
