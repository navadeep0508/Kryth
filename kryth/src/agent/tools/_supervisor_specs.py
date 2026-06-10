"""JSON-schema specs for the Execution Supervisor tools."""

from __future__ import annotations


SUPERVISOR_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "run_supervised_mission",
            "description": (
                "Execute a mission under full Execution Supervisor oversight. "
                "The supervisor continuously monitors health, predicts failures "
                "before each step, auto-recovers errors, dynamically replans when "
                "agents crash, enforces resource ownership, tracks budget, and "
                "records all outcomes to the Experience Engine for future improvement. "
                "Optionally pass steps= to execute specific commands under supervision."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "Human-readable mission description.",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of shell commands to execute under supervision.",
                    },
                    "token_limit": {
                        "type": "integer",
                        "description": "Max tokens before auto-stop (0 = no limit).",
                    },
                    "time_limit_seconds": {
                        "type": "number",
                        "description": "Max wall-clock seconds (0 = no limit).",
                    },
                    "agent_limit": {
                        "type": "integer",
                        "description": "Max agent spawns allowed (default 20).",
                    },
                    "auto_recover": {
                        "type": "boolean",
                        "description": "Automatically attempt recovery on failures (default true).",
                    },
                    "project_root": {
                        "type": "string",
                        "description": "Working directory for experience lookups.",
                    },
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "supervisor_status",
            "description": (
                "Render the full Execution Supervisor dashboard: mission health, "
                "budget consumption, agent statuses, resource ownership locks, "
                "and recent recovery events."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "supervisor_predict",
            "description": (
                "Predict failure risk for a command before running it. "
                "Returns: failure probability, risk level, likely errors, "
                "known repair commands, and suggested proactive fix. "
                "Use this before high-risk commands to pre-empt failures."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of what this step does.",
                    },
                    "project_root": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "supervisor_health",
            "description": (
                "Get a real-time health snapshot of all supervisor subsystems: "
                "agents, terminal, browser, budget. Returns overall health % "
                "and per-subsystem scores. "
                "Call this when a mission feels slow or something seems wrong."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "supervisor_replan",
            "description": (
                "Trigger dynamic replanning after a runtime failure. "
                "Redistributes remaining tasks to available agents and "
                "generates a new execution strategy without restarting the mission. "
                "Use when an agent crashes mid-execution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why replanning is needed.",
                    },
                    "failed_agent": {
                        "type": "string",
                        "description": "ID of the agent that crashed.",
                    },
                    "failed_step": {
                        "type": "string",
                        "description": "Step that failed.",
                    },
                    "available_agents": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Agent IDs available for reassignment.",
                    },
                    "remaining_tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Task IDs or descriptions still pending.",
                    },
                    "project_root": {"type": "string"},
                },
                "required": ["reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "supervisor_recover",
            "description": (
                "Manually trigger recovery for a specific failure. "
                "The recovery manager will classify the failure, apply the "
                "appropriate fix (install dep, restart browser, kill hung process, etc.), "
                "and record the outcome to the Experience Engine."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "failure_type": {
                        "type": "string",
                        "enum": [
                            "agent_crash", "terminal_hang", "browser_drop",
                            "dependency_missing", "permission_denied",
                            "timeout", "merge_conflict", "unknown",
                        ],
                    },
                    "description": {"type": "string"},
                    "command": {
                        "type": "string",
                        "description": "The command that failed.",
                    },
                    "error_output": {
                        "type": "string",
                        "description": "The error output from the failed command.",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "ID of the agent that encountered the failure.",
                    },
                    "project_root": {"type": "string"},
                },
                "required": ["failure_type", "description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ownership_claim",
            "description": (
                "Claim exclusive write ownership of a resource for an agent. "
                "Prevents other parallel agents from writing to the same file, "
                "symbol, port, or terminal worker. "
                "Always claim before writing; release when done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "resource": {
                        "type": "string",
                        "description": "File path, symbol name, port number, etc.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["file", "symbol", "directory", "port", "browser", "terminal"],
                        "description": "Resource kind (default: file).",
                    },
                },
                "required": ["agent_id", "resource"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ownership_release",
            "description": (
                "Release ownership of a resource or all resources owned by an agent. "
                "Call when an agent finishes writing to its claimed resources."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string"},
                    "resource": {
                        "type": "string",
                        "description": "Resource to release. Empty string = release all.",
                    },
                    "kind": {"type": "string"},
                },
                "required": ["agent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ownership_status",
            "description": (
                "Show the current resource ownership table — which agents own "
                "which files, ports, and directories, and for how long."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "budget_status",
            "description": (
                "Show current budget consumption: tokens, elapsed time, "
                "agent spawns, and tool calls. Warns when limits are approached."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
