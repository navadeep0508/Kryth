"""JSON-schema specs for Mission Graph & Workspace Intelligence tools."""

from __future__ import annotations

MISSION_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "mission_start",
            "description": (
                "Start a new persistent mission for the current project. "
                "Creates a named mission DAG that survives restarts. "
                "Optionally supply an initial task list. "
                "Returns the mission_id needed for all subsequent mission calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short human-readable mission name."},
                    "goal": {"type": "string", "description": "The high-level goal or objective."},
                    "tasks": {
                        "type": "array",
                        "description": "Optional initial task nodes. Each has id, description, depends_on (list of ids), tool, action.",
                        "items": {"type": "object"},
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_resume",
            "description": (
                "Resume the most recent incomplete mission (or a specific one by id). "
                "Restores the DAG state and returns pending/active tasks so work can continue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Specific mission id to resume. Omit to use the latest resumable mission."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_status",
            "description": (
                "Get the current status of a mission: DAG progress, task statuses, "
                "blocked tasks, and completion percentage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Mission id to query."},
                },
                "required": ["mission_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_add_task",
            "description": (
                "Dynamically add a new task node to a running mission. "
                "Use this when you discover additional work that needs to be tracked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Mission id."},
                    "task_id": {"type": "string", "description": "Unique node id within this mission."},
                    "description": {"type": "string", "description": "Human-readable task description."},
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of task ids this task waits for.",
                    },
                    "tool": {"type": "string", "description": "Tool to use (e.g. 'shell', 'browser', 'filesystem')."},
                    "action": {"type": "string", "description": "Action name (e.g. 'run_command', 'write_file')."},
                    "params": {"type": "object", "description": "Tool parameters."},
                },
                "required": ["mission_id", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_finish_task",
            "description": (
                "Mark a task node as completed. "
                "Optionally record artifacts (files, APIs, components) produced by this task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Mission id."},
                    "task_id": {"type": "string", "description": "Node id to complete."},
                    "result": {"type": "object", "description": "Optional result payload."},
                    "artifacts": {
                        "type": "array",
                        "description": "Files/APIs/components produced. Each: {path, kind, name, operation}.",
                        "items": {"type": "object"},
                    },
                },
                "required": ["mission_id", "task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_checkpoint",
            "description": (
                "Save a full subsystem checkpoint before risky operations. "
                "Captures: mission DAG, browser cookies/storage, terminal state, working memory. "
                "Call this before destructive commands, deployments, or large refactors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Mission id."},
                    "label": {"type": "string", "description": "Optional label for the checkpoint (e.g. 'pre-deploy')."},
                },
                "required": ["mission_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_impact_analysis",
            "description": (
                "Analyze the downstream impact of changed files. "
                "Returns affected modules, APIs, tests, and task nodes that need re-running. "
                "Call after any significant file change to understand what else needs attention."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "changed_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relative paths of files that were changed.",
                    },
                    "mission_id": {
                        "type": "string",
                        "description": "If provided, marks dependent task nodes as pending.",
                    },
                },
                "required": ["changed_paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mission_summary",
            "description": (
                "Return a Markdown dashboard of mission progress: progress bar, "
                "task list with status icons, elapsed time, retry count, and checkpoints."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Mission id."},
                },
                "required": ["mission_id"],
            },
        },
    },
]
