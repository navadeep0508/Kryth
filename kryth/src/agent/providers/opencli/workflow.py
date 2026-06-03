"""Workflow engine — converts a natural-language goal into sequential browser actions.

Input:  {"goal": "Apply for internship", "url": "...", "resume": "...", ...}
Output: list of Action steps → executed by Executor
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Action:
    action: str
    params: dict[str, Any] = field(default_factory=dict)


# Goal templates: ordered action sequences with {placeholder} context vars
_TEMPLATES: dict[str, list[dict]] = {
    "apply": [
        {"action": "open",          "params": {"url": "{url}"}},
        {"action": "detect_form"},
        {"action": "extract_fields"},
        {"action": "fill_form",     "params": {"data": "{data}"}},
        {"action": "upload_resume", "params": {"path": "{resume}"}},
        {"action": "submit"},
    ],
    "login": [
        {"action": "open",  "params": {"url": "{url}"}},
        {"action": "fill",  "params": {"selector": "input[type=email],input[name=email],input[name=username]", "value": "{username}"}},
        {"action": "fill",  "params": {"selector": "input[type=password]", "value": "{password}"}},
        {"action": "submit"},
    ],
    "search": [
        {"action": "open",    "params": {"url": "{url}"}},
        {"action": "fill",    "params": {"selector": "input[type=search],input[name=q],textarea[name=q]", "value": "{query}"}},
        {"action": "submit"},
        {"action": "extract", "params": {"selector": "{result_selector}"}},
    ],
    "extract": [
        {"action": "open",    "params": {"url": "{url}"}},
        {"action": "extract", "params": {"selector": "{selector}"}},
    ],
    "download": [
        {"action": "open",     "params": {"url": "{url}"}},
        {"action": "download", "params": {"url": "{url}", "output": "{output}"}},
    ],
    "fill": [
        {"action": "open",      "params": {"url": "{url}"}},
        {"action": "fill_form", "params": {"data": "{data}"}},
        {"action": "submit"},
    ],
}

_KEYWORD_MAP: dict[str, list[str]] = {
    "apply":    ["apply", "internship", "job", "application", "submit application"],
    "login":    ["login", "sign in", "authenticate", "log in", "signin"],
    "search":   ["search", "find", "look up", "query"],
    "extract":  ["extract", "scrape", "get data", "fetch", "read page"],
    "download": ["download", "save file", "save content"],
    "fill":     ["fill", "fill form", "complete form"],
}


def _match_template(goal: str) -> str:
    goal_lower = goal.lower()
    for template, keywords in _KEYWORD_MAP.items():
        if any(kw in goal_lower for kw in keywords):
            return template
    return "extract"


def _resolve(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("{") and value.endswith("}"):
        key = value[1:-1]
        return context.get(key, value)
    return value


class WorkflowEngine:
    """Converts a goal string into a sequential plan and executes it."""

    def plan(self, goal: str, context: dict[str, Any] | None = None) -> list[Action]:
        """Return the ordered list of Actions for the given goal."""
        context = context or {}
        template_key = _match_template(goal)
        template = _TEMPLATES.get(template_key, _TEMPLATES["extract"])
        actions = []
        for step in template:
            params = {k: _resolve(v, context) for k, v in step.get("params", {}).items()}
            actions.append(Action(action=step["action"], params=params))
        return actions

    def execute(self, goal: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Plan and execute a workflow. Returns a result summary dict."""
        from agent.providers.opencli.executor import Executor
        from agent.providers.opencli.session import get_manager

        context = context or {}
        plan = self.plan(goal, context)
        session_name = context.get("session", "default")

        get_manager().restore_session(session_name)
        executor = Executor(session_name)

        results: list[dict] = []
        errors: list[dict] = []

        for action in plan:
            result = executor.execute_action(action.action, action.params)
            entry = {"action": action.action, "result": result}
            results.append(entry)
            if result.startswith("[ERROR"):
                errors.append(entry)
                # Abort on navigation or submit failures — subsequent steps are meaningless
                if action.action in ("open", "submit"):
                    break

        return {
            "goal": goal,
            "template": _match_template(goal),
            "steps_planned": len(plan),
            "steps_run": len(results),
            "results": results,
            "errors": errors,
            "success": len(errors) == 0,
        }
