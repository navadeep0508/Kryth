"""Mission Router — maps a mission goal/tags to the right execution strategy."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from agent.mos.mission_state import Mission, ResourceBudget


@dataclass
class RoutingDecision:
    strategy:       str            # "planner_ual" | "direct_ual" | "research" | "monitoring"
    team_size:      int            # suggested max_workers
    use_browser:    bool
    use_cloud:      bool
    planner_hint:   str            # objective hint passed to UniversalPlanner
    executor_hints: list[str]      # preferred executors
    notes:          str = ""


_STRATEGY_RULES: list[tuple[str, RoutingDecision]] = [
    # pattern → RoutingDecision
    (
        r"deploy|release|publish|ship|docker|k8s|kubernetes|terraform|aws|gcp|azure|ci/cd|pipeline",
        RoutingDecision("planner_ual", 6, False, True,  "deploy", ["cloud", "terminal", "api"], "Cloud + terminal heavy"),
    ),
    (
        r"scrape|crawl|browser|screenshot|web|click|form|ui test",
        RoutingDecision("planner_ual", 3, True,  False, "browser_automation", ["browser", "api"], "Browser required"),
    ),
    (
        r"research|investigate|analyse|analyze|survey|audit|report",
        RoutingDecision("research",    2, True,  False, "research", ["browser", "api", "terminal"], "Research & synthesis"),
    ),
    (
        r"monitor|watch|alert|health check|ping|uptime",
        RoutingDecision("monitoring",  1, False, False, "monitoring", ["api", "terminal"], "Lightweight monitoring"),
    ),
    (
        r"auth|login|oauth|jwt|session|permission|role",
        RoutingDecision("planner_ual", 4, False, False, "auth", ["terminal", "api"], "Auth pattern"),
    ),
    (
        r"payment|stripe|billing|subscription|invoice",
        RoutingDecision("planner_ual", 4, False, False, "payment", ["terminal", "api"], "Payment integration"),
    ),
    (
        r"database|migration|schema|sql|postgres|mysql|mongo",
        RoutingDecision("planner_ual", 3, False, False, "database", ["terminal"], "Database work"),
    ),
    (
        r"test|spec|coverage|tdd|bdd|jest|pytest|vitest",
        RoutingDecision("planner_ual", 3, False, False, "testing", ["terminal"], "Testing focus"),
    ),
    (
        r"refactor|clean|rename|reorganize|restructure",
        RoutingDecision("planner_ual", 2, False, False, "refactor", ["terminal"], "Code refactor"),
    ),
    (
        r"fix|bug|error|exception|crash|debug",
        RoutingDecision("planner_ual", 3, False, False, "debug", ["terminal", "api"], "Debug mode"),
    ),
    (
        r"build|feature|implement|create|add|develop|saas",
        RoutingDecision("planner_ual", 5, False, False, "build", ["terminal", "api"], "Full build"),
    ),
]

_DEFAULT_ROUTE = RoutingDecision("planner_ual", 4, False, False, "general", ["terminal", "api"], "Default")


class MissionRouter:
    """Routes missions to execution strategies based on goal/tags."""

    def route(self, mission: Mission) -> RoutingDecision:
        text = (mission.goal + " " + mission.description + " " + " ".join(mission.tags)).lower()
        for pattern, decision in _STRATEGY_RULES:
            if re.search(pattern, text):
                return decision
        return _DEFAULT_ROUTE

    def adjust_budget(self, mission: Mission, decision: RoutingDecision) -> ResourceBudget:
        """Return a budget tuned to the routing decision."""
        b = mission.budget
        b.max_workers = max(b.max_workers, decision.team_size)
        if decision.use_browser:
            b.max_browser_tabs = max(b.max_browser_tabs, 2)
        if decision.use_cloud:
            b.max_executors = max(b.max_executors, 4)
        return b
