"""Rollback Planner — auto-generates rollback_steps for every action.

Maps action goal keywords to the appropriate rollback strategy so the
RollbackEngine can undo any action without the LLM specifying it.
"""

from __future__ import annotations

import re
from typing import Any


_RULES: list[tuple[str, list[str]]] = [
    # File creation → delete on rollback
    (r"write|create file|generate file",    ["delete_file:{path}"]),
    # File edit → backup first, restore on rollback
    (r"edit|modify|update file",            ["file_backup:{path}"]),
    # Git mutations → reset to previous HEAD
    (r"git commit|commit",                  ["git_reset:HEAD~1"]),
    (r"git push",                           []),   # can't un-push; noop
    (r"git tag",                            ["run:git tag -d {tag}"]),
    # Docker
    (r"docker build",                       ["run:docker rmi {image} --force"]),
    (r"docker push",                        []),   # noop
    # Package install
    (r"pip install",                        ["run:pip uninstall -y {package}"]),
    (r"npm install",                        ["run:npm uninstall {package}"]),
    # Database migration
    (r"migration|migrate",                  ["run:python manage.py migrate --reverse 1"]),
    # Deploy
    (r"deploy|release",                     ["run:echo MANUAL_ROLLBACK_REQUIRED"]),
    # General shell with no known rollback
    (r"build|compile|make|test|lint",       ["noop"]),
]

_DEFAULT = ["noop"]


class RollbackPlanner:
    """Generate rollback_steps for an action from its goal string and params."""

    def generate(self, goal: str, params: dict[str, Any] | None = None) -> list[str]:
        params = params or {}
        lower  = goal.lower()
        steps: list[str] = []

        for pattern, templates in _RULES:
            if re.search(pattern, lower):
                for t in templates:
                    step = self._fill(t, goal, params)
                    if step not in steps:
                        steps.append(step)

        return steps or _DEFAULT

    def _fill(self, template: str, goal: str, params: dict[str, Any]) -> str:
        if "{path}" in template:
            path = (
                params.get("path") or params.get("file")
                or self._extract_path(goal) or ""
            )
            return template.replace("{path}", path)

        if "{tag}" in template:
            tag = params.get("tag") or params.get("version") or "latest"
            return template.replace("{tag}", tag)

        if "{image}" in template:
            image = params.get("image") or params.get("name") or "kryth-image"
            return template.replace("{image}", image)

        if "{package}" in template:
            pkg = params.get("package") or params.get("name") or ""
            return template.replace("{package}", pkg)

        return template

    def _extract_path(self, goal: str) -> str:
        m = re.search(r"[\w./\\-]+\.\w{1,5}", goal)
        return m.group(0) if m else ""
