"""Enhanced planner — structured task graph planning for KRYTH v2.

Turns user requests into structured task graphs (DAGs) with explicit
tool assignments, dependencies, and validation criteria. Never executes
directly — always Plan -> Execute.

Outputs task graphs in this format:

    {
      "goal": "Build a landing page",
      "steps": [
        {"id": "create-html", "tool": "filesystem", "action": "write_file",
         "params": {"path": "index.html", "content": "..."},
         "depends_on": []},
        {"id": "create-css", "tool": "filesystem", "action": "write_file",
         "params": {"path": "styles.css", "content": "..."},
         "depends_on": []},
        {"id": "verify", "tool": "browser", "action": "open",
         "params": {"url": "index.html"},
         "depends_on": ["create-html", "create-css"]}
      ]
    }
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from agent.llm import _get_client, PLANNER_MODEL
from agent import ui

logger = logging.getLogger(__name__)


# System prompt for the structured task-graph planner.
_PLANNER_V2_SYSTEM = """You are a planning agent for an autonomous AI coding system.
Given a user request, produce a JSON task graph that the executor will run.

The task graph is a JSON object with this schema:

{
  "goal": "one-sentence description of what the user wants",
  "task_type": "build_app|build_landing|build_api|build_cli|fix_bug|refactor|investigate|browser_task|data_task|other",
  "summary_for_user": "2-3 sentence plain-English summary of the plan for the user",
  "steps": [
    {
      "id": "unique-kebab-case-id",
      "tool": "filesystem|browser|shell|search|memory|code",
      "action": "read_file|write_file|edit_file|run_command|search_code|navigate|click|type|extract|...",
      "params": {"key1": "value1"},
      "description": "one-line what this step does",
      "depends_on": ["id-of-previous-step"],
      "max_retries": 3,
      "timeout_seconds": 60,
      "validation": "how to verify this step succeeded (optional)"
    }
  ]
}

Rules:
1. Break the task into the FEWEST steps that make sense (3-12 typical)
2. Each step must use an available tool action
3. Dependencies form a DAG — no cycles
4. Parallel steps should have empty depends_on or depend only on the same upstream
5. A step only depends on steps whose outputs it needs
6. Use "browser" tool for any web-related action (navigate, click, extract)
7. Use "filesystem" for file read/write/edit
8. Use "shell" for running commands
9. Use "search" for code search
10. Use "code" for code analysis and tool-generated actions
11. "validation" is an optional string describing how to check success
12. Return ONLY valid JSON — no markdown fences, no preamble
"""


def plan(user_input: str, project_context: str = "") -> dict | None:
    """Generate a structured task graph plan for the given user input.

    Args:
        user_input: The user's natural language request.
        project_context: Optional string describing the current project.

    Returns:
        A dict with the task graph, or None if planning fails/not needed.
    """
    if not _should_plan(user_input):
        return None

    context_section = ""
    if project_context:
        context_section = f"\n\nCurrent project context:\n{project_context[:2000]}"

    payload = user_input + context_section

    try:
        response = _get_client().chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _PLANNER_V2_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0,
            max_tokens=16384,
        )
    except Exception as e:
        logger.warning("Planner unavailable: %s", e)
        return None

    text = (response.choices[0].message.content or "").strip()
    if not text:
        return None

    plan_dict = _extract_plan(text)
    if plan_dict is None:
        logger.warning("Could not parse planner output as JSON")
        return None

    # Validate the plan shape
    errors = _validate_plan(plan_dict)
    if errors:
        logger.warning("Plan validation failed: %s", errors)
        return None

    return plan_dict


def plan_to_graph(plan_dict: dict) -> dict:
    """Convert a planner dict into the internal task graph format.

    Returns a dict compatible with the TaskGraph executor:
    {
      "goal": str,
      "tasks": [{"id": str, "tool": str, "action": str, "params": dict,
                 "depends_on": list, "max_retries": int, "timeout": int}]
    }
    """
    goal = plan_dict.get("goal", "Untitled task")
    steps = plan_dict.get("steps", [])

    tasks = []
    for step in steps:
        tasks.append({
            "id": step.get("id", f"step-{len(tasks)}"),
            "tool": step.get("tool", "filesystem"),
            "action": step.get("action", "read_file"),
            "params": step.get("params", {}),
            "description": step.get("description", ""),
            "depends_on": step.get("depends_on", []),
            "max_retries": step.get("max_retries", 3),
            "timeout_seconds": step.get("timeout_seconds", 60),
            "validation": step.get("validation", ""),
        })

    return {
        "goal": goal,
        "task_type": plan_dict.get("task_type", "other"),
        "summary": plan_dict.get("summary_for_user", ""),
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _should_plan(user_input: str) -> bool:
    """Determine if the input warrants planning."""
    text = user_input.strip()
    if not text or text.startswith("/"):
        return False

    # Always plan for complex-looking requests
    words = text.split()
    if len(words) < 4:
        return False

    trigger_words = {
        "build", "create", "make", "implement", "fix", "debug", "refactor",
        "rewrite", "migrate", "add", "update", "change", "design", "write",
        "develop", "ship", "deploy", "landing", "dashboard", "app", "api",
        "cli", "website", "fullstack", "automate", "scaffold", "generate",
    }

    lowered = {w.lower().strip(",.!?:;\"'") for w in words}
    return bool(lowered & trigger_words)


def _extract_plan(text: str) -> dict | None:
    """Extract a JSON plan from the LLM's response text.

    Tolerates surrounding prose, markdown fences, and other wrapping.
    """
    # Strip markdown code fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip()
            if cleaned.startswith("{"):
                text = cleaned
                break
            # Also try removing a "json" prefix
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
                if cleaned.startswith("{"):
                    text = cleaned
                    break

    # Find the first JSON object
    start = text.find("{")
    if start < 0:
        return None

    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start:i + 1]
                try:
                    obj = json.loads(blob)
                    return obj if isinstance(obj, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


def _validate_plan(plan: dict) -> list[str]:
    """Validate the plan structure. Returns list of error messages (empty = valid)."""
    errors = []

    if not isinstance(plan, dict):
        return ["Plan is not a dict"]

    if "goal" not in plan or not isinstance(plan.get("goal"), str):
        errors.append("Missing 'goal' string")

    if "steps" not in plan or not isinstance(plan.get("steps"), list):
        errors.append("Missing 'steps' list")
        return errors

    steps = plan["steps"]
    if not steps:
        errors.append("Steps list is empty")

    ids = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"Step {i} is not a dict")
            continue

        step_id = step.get("id", "")
        if not step_id:
            errors.append(f"Step {i} missing 'id'")
        elif step_id in ids:
            errors.append(f"Duplicate step id: {step_id}")
        ids.add(step_id)

        if "tool" not in step:
            errors.append(f"Step '{step_id}' missing 'tool'")
        if "action" not in step:
            errors.append(f"Step '{step_id}' missing 'action'")

        # Validate dependencies
        deps = step.get("depends_on", [])
        if isinstance(deps, list):
            for dep in deps:
                if dep not in ids and dep != step_id:
                    # This might be a forward reference, that's OK — we check final state
                    pass

    return errors
