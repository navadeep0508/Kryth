"""JSON-schema specs for Autonomous Software Factory tools."""

from __future__ import annotations

FACTORY_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "factory_build",
            "description": (
                "Start an autonomous software build from a high-level product goal. "
                "Generates epics, stories, and a sprint, creates a persistent mission, "
                "and returns the mission_id + sprint_id to track progress. "
                "Example goal: 'Build an AI SaaS with Stripe billing and React frontend'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "High-level product goal."},
                    "project_name": {"type": "string", "description": "Optional project name."},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_status",
            "description": "Get the current factory status: mission progress, backlog summary, active sprints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Optional mission id for detailed progress."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_sprint_start",
            "description": "Start a sprint, creating a mission for its stories and transitioning them to in-progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sprint_id": {"type": "string", "description": "Sprint id to start."},
                },
                "required": ["sprint_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_sprint_complete",
            "description": "Complete a sprint, record outcomes, trigger reflection, and return a sprint summary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sprint_id": {"type": "string", "description": "Sprint id to complete."},
                },
                "required": ["sprint_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_architecture_audit",
            "description": (
                "Run an architecture audit: file sizes, hardcoded secrets, "
                "circular imports, test presence, module boundaries. "
                "Returns violations and passed checks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "Project root to audit (default: current directory)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_code_review",
            "description": (
                "Run automated code review over changed files or the entire project. "
                "Checks security, complexity, and style. "
                "Returns findings grouped by category and severity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths to review. Omit to review all .py files.",
                    },
                    "root": {"type": "string", "description": "Project root (default: current directory)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_run_tests",
            "description": (
                "Run the project's test suites (unit, integration, build). "
                "Auto-detects pytest/npm/make. Returns pass/fail counts per suite."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "suite": {
                        "type": "string",
                        "description": "Specific suite to run: unit | integration | build. Omit to run all.",
                    },
                    "root": {"type": "string", "description": "Project root."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_deploy",
            "description": (
                "Run the deployment pipeline: build → deploy → health check. "
                "Production deployments require approval_confirmed=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "env": {
                        "type": "string",
                        "enum": ["staging", "production"],
                        "description": "Target environment.",
                    },
                    "mission_id": {"type": "string", "description": "Mission id for checkpoint creation."},
                    "approval_confirmed": {
                        "type": "boolean",
                        "description": "Required true for production deployments.",
                    },
                },
                "required": ["env"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_maintenance_scan",
            "description": (
                "Scan a completed project for health issues: failing tests, "
                "stale dependencies, security warnings, and architecture drift. "
                "Returns a maintenance report with suggested fixes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "root": {"type": "string", "description": "Project root to scan."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "factory_dashboard",
            "description": (
                "Render the executive project dashboard: progress bar, "
                "team statuses, risk level, success prediction, and deployment state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mission_id": {"type": "string", "description": "Mission id for progress data."},
                    "project_name": {"type": "string", "description": "Project name override."},
                },
                "required": [],
            },
        },
    },
]
