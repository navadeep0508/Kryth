"""JSON-schema specs for the terminal engine tools."""

from __future__ import annotations


TERMINAL_TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": (
                "Execute a command in KRYTH's persistent terminal engine. "
                "Unlike run_command, the shell is long-lived: cwd, env, and venv "
                "persist across calls. Output is auto-parsed and compressed — "
                "only the error signal is returned to you, not 5000 raw lines. "
                "Use worker= to route to a specific isolated terminal (backend, frontend, tests, docker)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds before killing (1-600, default 30).",
                    },
                    "worker": {
                        "type": "string",
                        "description": "Worker terminal: backend, frontend, tests, docker, misc.",
                    },
                    "compress_output": {
                        "type": "boolean",
                        "description": "Compress verbose output before returning (default true).",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_state",
            "description": (
                "Get the current terminal environment state: cwd, git branch, "
                "active virtualenv, node version, open ports, running servers, "
                "docker containers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Force a fresh probe instead of using the cache.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_plan",
            "description": (
                "Build and preview a smart execution plan for a goal. "
                "Detects project type from marker files and generates the correct "
                "commands (e.g. 'uv run pytest' for Python uv projects). "
                "Use this to review the plan before running it with shell_run_plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "test | build | install",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (default '.').",
                    },
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_run_plan",
            "description": (
                "Execute a smart execution plan with automatic error recovery. "
                "Detects project type, runs the correct command sequence "
                "(check env → install deps → build → test), and automatically "
                "recovers from common errors (missing deps, network failures) "
                "before retrying. Reports structured success/failure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "test | build | install",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory (default '.').",
                    },
                    "max_retries": {
                        "type": "integer",
                        "description": "Auto-recovery attempts per step (default 2).",
                    },
                    "worker": {
                        "type": "string",
                        "description": "Worker terminal to use (default misc).",
                    },
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_build_test_loop",
            "description": (
                "Run a build → test → fix → retry loop until tests pass "
                "or the iteration limit is reached. Uses structured output "
                "parsing to detect failures automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "build_command": {"type": "string"},
                    "test_command": {"type": "string"},
                    "fix_hint": {
                        "type": "string",
                        "description": "Optional hint for the fix to attempt on failure.",
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Max build-test cycles (default 5).",
                    },
                    "worker": {"type": "string"},
                },
                "required": ["build_command", "test_command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_list",
            "description": (
                "List all processes tracked by KRYTH: PID, status, CPU%, RAM, "
                "listening ports, and command. Use this to check what is running."
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
            "name": "process_kill",
            "description": "Stop or force-kill a process by PID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pid": {"type": "integer"},
                    "force": {
                        "type": "boolean",
                        "description": "If true, send SIGKILL immediately (default false = SIGTERM).",
                    },
                },
                "required": ["pid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "terminal_memory_recall",
            "description": (
                "Recall past terminal workflows and successful command sequences. "
                "Pass a goal ('test', 'build', 'deploy') to get the remembered "
                "command sequence for that workflow, or omit for a summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Workflow name to recall, or '' for summary.",
                    }
                },
                "required": [],
            },
        },
    },
]
