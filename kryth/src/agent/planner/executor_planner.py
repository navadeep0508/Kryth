"""Executor Planner — assigns preferred_executors to each action.

Maps action goal keywords to the best executor chain (primary + fallbacks).
The ExecutorSelector uses these hints when scoring at runtime.
"""

from __future__ import annotations

import re
from typing import Any


# (pattern, [primary, fallback1, fallback2, ...])
_EXECUTOR_RULES: list[tuple[str, list[str]]] = [
    # Cloud / deploy
    (r"deploy to (aws|gcp|azure|cloud|k8s|kubernetes)", ["cloud", "terminal", "api"]),
    (r"cloud|infrastructure|terraform|helm",             ["cloud", "terminal"]),
    # Docker
    (r"docker|container",                                ["terminal"]),
    # Git
    (r"\bgit\b|commit|push tag|branch",                  ["terminal"]),
    # Build / compile / test
    (r"build|compile|make|lint|format|test|pytest|jest",["terminal"]),
    # Install
    (r"install|pip|npm|yarn|cargo",                      ["terminal"]),
    # API calls
    (r"api call|http|rest|graphql|webhook|github|slack|stripe|aws sdk",
                                                         ["api", "terminal"]),
    # File operations
    (r"create file|write file|edit file|delete file|generate file",
                                                         ["terminal"]),
    # Browser
    (r"navigate|browse|open url|click|form|upload|download|scrape|web",
                                                         ["browser", "api", "desktop"]),
    # Desktop / native
    (r"dialog|native ui|file picker|vs ?code|explorer",  ["desktop", "browser"]),
    # Health check / verify endpoint
    (r"health check|healthcheck|ping url",               ["api", "terminal"]),
    # Database
    (r"migration|migrate|seed|sql|database",             ["terminal"]),
]

_DEFAULT_EXECUTORS = ["terminal", "api"]


class ExecutorPlanner:
    """Assigns preferred executors to an action based on its goal."""

    def assign(self, goal: str, params: dict[str, Any] | None = None) -> list[str]:
        lower = goal.lower()
        for pattern, executors in _EXECUTOR_RULES:
            if re.search(pattern, lower):
                return executors
        return _DEFAULT_EXECUTORS
