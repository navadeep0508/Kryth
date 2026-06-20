"""Decomposition Engine — recursively breaks an Objective into action items.

Strategy:
  1. Use a fast heuristic decomposition for known patterns (zero LLM cost).
  2. Fall back to `ask_planner()` from llm.py for novel objectives.
  3. LLM output is validated and normalised into a flat action list.

The decomposition produces raw action dicts (not Action objects) that are
later enriched by the specialist planners (executor, verification, rollback)
inside ActionGraphBuilder.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from agent.planner.planner_state import Complexity, Objective

logger = logging.getLogger(__name__)


# ── Heuristic templates ───────────────────────────────────────────────────────

def _auth_template(obj: Objective) -> list[dict]:
    return [
        {"id": "design_schema",   "goal": "Design user model and schema",        "priority": 10},
        {"id": "create_models",   "goal": "Create database models",              "priority": 20},
        {"id": "create_migration","goal": "Generate and run database migration",  "priority": 30},
        {"id": "create_service",  "goal": "Create JWT authentication service",    "priority": 40},
        {"id": "create_middleware","goal": "Create authentication middleware",     "priority": 50},
        {"id": "create_routes",   "goal": "Create auth routes (login/register/refresh)", "priority": 60},
        {"id": "write_tests",     "goal": "Write unit and integration tests",     "priority": 70},
        {"id": "validate_security","goal": "Validate security: OWASP checks",     "priority": 80},
        {"id": "update_docs",     "goal": "Update API documentation",             "priority": 90},
    ]

def _deploy_template(obj: Objective) -> list[dict]:
    return [
        {"id": "build_app",       "goal": "Build application",                   "priority": 10},
        {"id": "run_tests",       "goal": "Run full test suite",                  "priority": 20},
        {"id": "build_image",     "goal": "Build Docker image",                   "priority": 30},
        {"id": "push_tag",        "goal": "Push git tag and release",             "priority": 35},
        {"id": "upload_assets",   "goal": "Upload release assets",                "priority": 40},
        {"id": "deploy_cloud",    "goal": "Deploy to cloud infrastructure",       "priority": 50},
        {"id": "health_check",    "goal": "Health check deployment endpoint",     "priority": 60},
        {"id": "notify_team",     "goal": "Notify team of deployment",            "priority": 70},
    ]

def _saas_template(obj: Objective) -> list[dict]:
    return [
        {"id": "setup_repo",      "goal": "Initialise repository and project structure", "priority": 5},
        {"id": "design_api",      "goal": "Design API schema and contracts",      "priority": 10},
        {"id": "create_models",   "goal": "Create database models and migrations","priority": 20},
        {"id": "build_backend",   "goal": "Build backend services and routes",    "priority": 30},
        {"id": "build_frontend",  "goal": "Build frontend UI components",         "priority": 30},
        {"id": "write_tests",     "goal": "Write backend and frontend tests",     "priority": 40},
        {"id": "build_docker",    "goal": "Build and tag Docker image",           "priority": 50},
        {"id": "run_ci",          "goal": "Run CI pipeline (lint, test, build)",  "priority": 55},
        {"id": "deploy_staging",  "goal": "Deploy to staging environment",        "priority": 60},
        {"id": "verify_staging",  "goal": "Verify staging health checks",         "priority": 65},
        {"id": "deploy_prod",     "goal": "Deploy to production",                 "priority": 70},
        {"id": "verify_prod",     "goal": "Verify production deployment",         "priority": 75},
        {"id": "update_docs",     "goal": "Update documentation and changelog",   "priority": 80},
        {"id": "notify_team",     "goal": "Notify team of release",               "priority": 90},
    ]

def _generic_template(obj: Objective) -> list[dict]:
    """Fallback: one action per domain."""
    actions = []
    for i, domain in enumerate(obj.domains):
        actions.append({
            "id":       f"{domain}_task",
            "goal":     f"{obj.goal} — {domain} implementation",
            "priority": (i + 1) * 10,
        })
    if not actions:
        actions = [{"id": "main_task", "goal": obj.goal, "priority": 10}]
    return actions


_HEURISTIC_PATTERNS: list[tuple[str, Any]] = [
    (r"(auth|jwt|login|register|oauth)",          _auth_template),
    (r"(deploy|release|ship|publish)",             _deploy_template),
    (r"(saas|full.stack|entire app|complete app)", _saas_template),
]


class DecompositionEngine:
    """Break an Objective into a flat list of action dicts."""

    def decompose(self, objective: Objective) -> list[dict[str, Any]]:
        # 1. Try heuristic templates
        lower = objective.raw.lower()
        for pattern, fn in _HEURISTIC_PATTERNS:
            if re.search(pattern, lower):
                logger.info("Heuristic template matched: %s", pattern)
                return fn(objective)

        # 2. Try the existing ask_planner()
        try:
            plan, _ = self._call_llm_planner(objective)
            if plan and "steps" in plan:
                return self._normalise(plan["steps"])
            if plan and "actions" in plan:
                return self._normalise(plan["actions"])
        except Exception as exc:
            logger.warning("LLM decomposition failed (%s), using generic template", exc)

        # 3. Generic fallback
        return _generic_template(objective)

    # ── LLM call ──────────────────────────────────────────────────────

    def _call_llm_planner(self, objective: Objective) -> tuple[Optional[dict], str]:
        from agent.llm import ask_planner   # type: ignore
        prompt = (
            f"Decompose this objective into a JSON action plan.\n\n"
            f"Objective: {objective.goal}\n"
            f"Domains: {', '.join(objective.domains)}\n"
            f"Requirements: {', '.join(objective.requirements)}\n\n"
            f"Return JSON with 'mission' and 'actions' list. Each action: "
            f"id, goal, priority (int), dependencies (list of ids)."
        )
        return ask_planner(prompt)

    # ── Normalise ─────────────────────────────────────────────────────

    def _normalise(self, raw: list[dict]) -> list[dict]:
        """Ensure each item has id, goal, priority, dependencies."""
        out = []
        for i, item in enumerate(raw):
            out.append({
                "id":           item.get("id", f"step_{i}"),
                "goal":         item.get("goal") or item.get("description", f"step_{i}"),
                "priority":     int(item.get("priority", (i + 1) * 10)),
                "dependencies": list(item.get("dependencies", item.get("depends_on", []))),
                "params":       dict(item.get("params", {})),
            })
        return out
