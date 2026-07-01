"""ProductPlanner — convert a high-level user goal into engineering epics and stories.

Reuses:
- ``agent.planner.plan()`` for structured task decomposition
- ``agent.orchestration.repo_intelligence.analyze_repo()`` for project awareness
- ``agent.factory.backlog_manager.BacklogManager`` to persist the result
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

@dataclass
class ProductPlan:
    goal: str
    project_name: str
    epics: list[dict] = field(default_factory=list)
    tech_stack: dict = field(default_factory=dict)
    risk_notes: list[str] = field(default_factory=list)
    backlog_ids: dict = field(default_factory=dict)  # epic_title → epic_id

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "project_name": self.project_name,
            "epics": self.epics,
            "tech_stack": self.tech_stack,
            "risk_notes": self.risk_notes,
            "backlog_ids": self.backlog_ids,
        }


# ---------------------------------------------------------------------------
# Default epic templates per goal keyword
# ---------------------------------------------------------------------------

_EPIC_TEMPLATES: dict[str, list[dict]] = {
    "saas": [
        {"title": "Architecture & Setup", "stories": ["Project scaffold", "CI/CD pipeline", "Environment config"]},
        {"title": "Authentication", "stories": ["User registration", "Login/logout", "JWT/session management", "OAuth"]},
        {"title": "Frontend", "stories": ["Design system", "Landing page", "Dashboard", "Responsive layout"]},
        {"title": "Backend API", "stories": ["REST routes", "Middleware", "Error handling", "Rate limiting"]},
        {"title": "Database", "stories": ["Schema design", "Migrations", "ORM setup", "Indexing"]},
        {"title": "Billing", "stories": ["Payment integration", "Subscription plans", "Webhooks", "Invoice generation"]},
        {"title": "Testing", "stories": ["Unit tests", "Integration tests", "API tests", "Browser tests"]},
        {"title": "Deployment", "stories": ["Docker config", "Cloud deployment", "Health checks", "Monitoring"]},
    ],
    "api": [
        {"title": "Architecture", "stories": ["Project scaffold", "OpenAPI spec", "Auth middleware"]},
        {"title": "Routes", "stories": ["CRUD endpoints", "Validation", "Error responses"]},
        {"title": "Database", "stories": ["Schema", "Migrations", "Queries"]},
        {"title": "Testing", "stories": ["Unit tests", "Integration tests", "Load tests"]},
        {"title": "Deployment", "stories": ["Containerize", "Deploy", "Monitor"]},
    ],
    "website": [
        {"title": "Design", "stories": ["Wireframes", "Design system", "Assets"]},
        {"title": "Frontend", "stories": ["Landing page", "About", "Contact", "SEO"]},
        {"title": "Performance", "stories": ["Optimization", "Caching", "CDN"]},
        {"title": "Deployment", "stories": ["Static hosting", "Domain setup", "SSL"]},
    ],
    "default": [
        {"title": "Planning & Architecture", "stories": ["Requirements", "Tech stack", "Project structure"]},
        {"title": "Core Implementation", "stories": ["Core features", "Data layer", "Business logic"]},
        {"title": "Testing", "stories": ["Unit tests", "Integration tests"]},
        {"title": "Polish & Deployment", "stories": ["Refactor", "Documentation", "Deploy"]},
    ],
}


class ProductPlanner:
    """Convert a user goal into a structured engineering plan with epics and stories.

    Parameters
    ----------
    project_hash:
        Project identity.
    backlog:
        BacklogManager instance to persist the plan.
    """

    def __init__(self, project_hash: str, backlog: Any = None) -> None:
        self.project_hash = project_hash
        self._backlog = backlog

    def plan(self, goal: str, project_name: str = "", project_root: str = ".") -> ProductPlan:
        """Generate a ProductPlan from a user goal.

        1. Detect project context via repo_intelligence
        2. Select epic templates based on goal keywords
        3. Persist epics + stories into the backlog
        4. Return the plan
        """
        if not project_name:
            project_name = _infer_project_name(goal)

        # Detect repo state
        repo_profile = _analyze_repo_safe(project_root)

        # Select epic templates
        epics_template = _select_template(goal, repo_profile)

        plan = ProductPlan(
            goal=goal,
            project_name=project_name,
            epics=epics_template,
            tech_stack=_infer_tech_stack(goal, repo_profile),
            risk_notes=_infer_risks(goal, repo_profile),
        )

        # Persist to backlog
        if self._backlog is not None:
            plan.backlog_ids = self._persist_to_backlog(plan)

        logger.info("ProductPlan created: %d epics for '%s'", len(epics_template), goal[:60])
        return plan

    def plan_from_repo(self, project_root: str) -> ProductPlan:
        """Infer a plan from an existing repository (maintenance / continuation mode)."""
        profile = _analyze_repo_safe(project_root)
        goal = f"Continue development of {profile.get('root', project_root)}"
        return self.plan(goal, project_root=project_root)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist_to_backlog(self, plan: ProductPlan) -> dict:
        ids: dict = {}
        for epic_def in plan.epics:
            eid = self._backlog.add_epic(
                title=epic_def["title"],
                description=epic_def.get("description", ""),
                priority=epic_def.get("priority", 50),
            )
            ids[epic_def["title"]] = eid
            for story_title in epic_def.get("stories", []):
                self._backlog.add_story(eid, title=story_title)
        return ids


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _analyze_repo_safe(root: str) -> dict:
    return {}


def _select_template(goal: str, repo: dict) -> list[dict]:
    g = goal.lower()
    if any(k in g for k in ("saas", "stripe", "subscription", "billing", "multi-tenant")):
        return _EPIC_TEMPLATES["saas"]
    if any(k in g for k in ("api", "rest", "fastapi", "express", "backend service")):
        return _EPIC_TEMPLATES["api"]
    if any(k in g for k in ("website", "landing", "portfolio", "static")):
        return _EPIC_TEMPLATES["website"]
    return _EPIC_TEMPLATES["default"]


def _infer_tech_stack(goal: str, repo: dict) -> dict:
    stack: dict = {}
    langs = repo.get("languages", [])
    frameworks = repo.get("frameworks", [])
    if langs:
        stack["languages"] = langs
    if frameworks:
        stack["frameworks"] = frameworks
    g = goal.lower()
    if "react" in g:
        stack.setdefault("frontend", "React")
    if "next" in g:
        stack.setdefault("frontend", "Next.js")
    if "fastapi" in g or "python" in g:
        stack.setdefault("backend", "FastAPI")
    if "django" in g:
        stack.setdefault("backend", "Django")
    if "postgres" in g or "postgresql" in g:
        stack.setdefault("database", "PostgreSQL")
    if "stripe" in g:
        stack.setdefault("payments", "Stripe")
    return stack


def _infer_risks(goal: str, repo: dict) -> list[str]:
    risks = []
    g = goal.lower()
    if any(k in g for k in ("payment", "billing", "stripe")):
        risks.append("Payment integration requires PCI compliance awareness")
    if any(k in g for k in ("deploy", "production", "cloud")):
        risks.append("Deployment to production requires approval checkpoint")
    if any(k in g for k in ("migrate", "migration", "refactor")):
        risks.append("Data migrations are irreversible — checkpoint required")
    if repo.get("has_database"):
        risks.append("Existing database — schema changes need careful migration")
    return risks


def _infer_project_name(goal: str) -> str:
    words = goal.strip().split()[:4]
    return " ".join(w.capitalize() for w in words) or "KRYTH Project"
