"""Objective Parser — converts natural language into a structured Objective.

Runs a lightweight keyword + heuristic pass first (zero LLM cost for
obvious cases), then optionally calls the LLM for ambiguous inputs.
"""

from __future__ import annotations

import re
from typing import Optional

from agent.planner.planner_state import Objective, Complexity


# ── Domain keyword maps ────────────────────────────────────────────────────────

_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "backend":    ["api", "endpoint", "route", "controller", "service", "model",
                   "database", "db", "server", "auth", "jwt", "middleware", "orm"],
    "frontend":   ["ui", "component", "react", "vue", "angular", "page", "form",
                   "css", "html", "tailwind", "frontend", "web", "button", "modal"],
    "testing":    ["test", "spec", "pytest", "jest", "unit", "integration", "e2e",
                   "coverage", "mock", "fixture", "qa"],
    "deployment": ["deploy", "docker", "kubernetes", "k8s", "ci", "cd", "pipeline",
                   "release", "ship", "publish", "heroku", "aws", "gcp", "azure",
                   "terraform", "helm"],
    "database":   ["migration", "schema", "postgres", "mysql", "sqlite", "mongo",
                   "redis", "seed", "index", "table", "column"],
    "security":   ["auth", "jwt", "oauth", "ssl", "tls", "xss", "csrf", "sanitize",
                   "encrypt", "hash", "permission", "role"],
    "documentation": ["doc", "readme", "swagger", "openapi", "comment", "docstring",
                      "wiki", "changelog"],
    "devops":     ["dockerfile", "compose", "nginx", "proxy", "ssl", "cert",
                   "monitor", "log", "alert"],
    "browser":    ["scrape", "navigate", "browse", "website", "form", "click",
                   "upload", "download", "session", "chrome"],
}

_CONSTRAINT_PATTERNS = [
    (r"no breaking change", "no breaking changes"),
    (r"keep existing api", "keep existing API"),
    (r"backward compat", "backward compatible"),
    (r"production.safe", "production-safe only"),
    (r"no new depend", "no new dependencies"),
    (r"minimal change", "minimal changes"),
]

_COMPLEXITY_SIGNALS: dict[Complexity, list[str]] = {
    Complexity.MASSIVE: ["saas", "platform", "full stack", "entire", "complete app",
                         "production ready", "enterprise"],
    Complexity.LARGE:   ["build", "create", "implement", "add feature", "system",
                         "module", "authentication", "deploy"],
    Complexity.MEDIUM:  ["fix", "update", "refactor", "improve", "extend", "add"],
    Complexity.SMALL:   ["rename", "move", "delete", "format", "typo", "comment"],
}


class ObjectiveParser:
    """Parse a raw user string into a structured Objective."""

    def parse(self, raw: str, context: dict | None = None) -> Objective:
        raw = raw.strip()
        goal      = self._clean_goal(raw)
        domains   = self._detect_domains(raw)
        reqs      = self._infer_requirements(raw, domains)
        constraints = self._detect_constraints(raw)
        complexity  = self._estimate_complexity(raw, domains)

        return Objective(
            raw=raw,
            goal=goal,
            domains=domains,
            requirements=reqs,
            constraints=constraints,
            complexity_hint=complexity,
            context=context or {},
        )

    # ── Helpers ───────────────────────────────────────────────────────

    def _clean_goal(self, raw: str) -> str:
        """Strip filler words; produce a clean imperative goal."""
        text = raw.strip().rstrip(".")
        # Capitalise first word
        return text[:1].upper() + text[1:] if text else text

    def _detect_domains(self, text: str) -> list[str]:
        lower = text.lower()
        found = []
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                found.append(domain)
        # Always include backend if nothing else matched
        if not found:
            found = ["backend"]
        return found

    def _infer_requirements(self, text: str, domains: list[str]) -> list[str]:
        reqs = []
        lower = text.lower()

        # Security always implied when auth is present
        if "security" in domains or any(kw in lower for kw in ["auth", "jwt", "oauth"]):
            if "security" not in reqs:
                reqs.append("Security validation")

        # Tests implied for any build/create task
        if any(w in lower for w in ["build", "create", "implement", "add"]):
            reqs.append("Unit tests")

        if "deployment" in domains:
            reqs.append("Health check endpoint")

        if "database" in domains or "backend" in domains:
            reqs.append("Database migrations")

        if "documentation" not in domains and any(
            w in lower for w in ["build", "create", "implement"]
        ):
            reqs.append("API documentation")

        return reqs

    def _detect_constraints(self, text: str) -> list[str]:
        lower = text.lower()
        found = []
        for pattern, label in _CONSTRAINT_PATTERNS:
            if re.search(pattern, lower):
                found.append(label)
        return found

    def _estimate_complexity(self, text: str, domains: list[str]) -> Complexity:
        lower = text.lower()
        for complexity, signals in _COMPLEXITY_SIGNALS.items():
            if any(s in lower for s in signals):
                # Scale up if many domains
                if len(domains) >= 4 and complexity == Complexity.LARGE:
                    return Complexity.MASSIVE
                return complexity
        # Domain count heuristic
        if len(domains) >= 5:
            return Complexity.MASSIVE
        if len(domains) >= 3:
            return Complexity.LARGE
        if len(domains) >= 2:
            return Complexity.MEDIUM
        return Complexity.SMALL
