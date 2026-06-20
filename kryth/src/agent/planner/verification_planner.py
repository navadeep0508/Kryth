"""Verification Planner — auto-generates verification_steps for every action.

Maps action goal keywords to the appropriate verification strategy so the
ActionVerifier knows exactly how to confirm success without the LLM having
to specify it explicitly.
"""

from __future__ import annotations

import re
from typing import Any


# (pattern, verification_step_template)
# {goal} is substituted with the action goal; {path} extracted when present
_RULES: list[tuple[str, str]] = [
    # File operations
    (r"write|create file|generate file",    "check_file:{path}"),
    (r"edit|modify|update file",            "check_not_empty:{path}"),
    (r"delete file",                        "check_no_error"),
    # Shell / build
    (r"build|compile|make|cargo|npm run",   "check_exit_0"),
    (r"install|pip install|npm install",    "check_exit_0"),
    (r"run test|pytest|jest|test suite",    "check_output:passed"),
    (r"lint|format|ruff|eslint",            "check_exit_0"),
    # Git
    (r"git commit|commit changes",          "check_exit_0"),
    (r"git push|push to",                   "check_exit_0"),
    (r"git tag",                            "check_exit_0"),
    # Docker
    (r"docker build",                       "check_exit_0"),
    (r"docker push",                        "check_exit_0"),
    (r"docker run|container start",         "check_no_error"),
    # Deploy
    (r"deploy|release",                     "check_no_error"),
    (r"health check|healthcheck",           "check_url:{url}"),
    # API
    (r"api call|http|request|post|get ",    "check_no_error"),
    # Browser
    (r"navigate|open url|browse",           "check_no_error"),
    (r"click|submit form",                  "check_no_error"),
    (r"upload",                             "check_no_error"),
    # General
    (r"verify|check|validate",              "check_exit_0"),
]

_DEFAULT = "check_no_error"


class VerificationPlanner:
    """Generate verification_steps for an action from its goal string."""

    def generate(self, goal: str, params: dict[str, Any] | None = None) -> list[str]:
        params = params or {}
        lower  = goal.lower()
        steps: list[str] = []

        for pattern, template in _RULES:
            if re.search(pattern, lower):
                step = self._fill(template, goal, params)
                if step not in steps:
                    steps.append(step)

        if not steps:
            steps = [_DEFAULT]

        return steps

    def _fill(self, template: str, goal: str, params: dict[str, Any]) -> str:
        # {path}: try params["path"] / params["file"] / params["dest"]
        if "{path}" in template:
            path = (
                params.get("path")
                or params.get("file")
                or params.get("dest")
                or self._extract_path_from_goal(goal)
            )
            return template.replace("{path}", path or "")

        # {url}: try params["url"] or goal heuristic
        if "{url}" in template:
            url = params.get("url") or params.get("endpoint") or "http://localhost:8000/health"
            return template.replace("{url}", url)

        return template

    def _extract_path_from_goal(self, goal: str) -> str:
        m = re.search(r"[\w./\\-]+\.\w{1,5}", goal)
        return m.group(0) if m else ""
