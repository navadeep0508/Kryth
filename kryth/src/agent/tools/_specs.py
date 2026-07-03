"""JSON-schema specs for the CORE 22 tools only.

Browser/OpenCLI/subagent/research tools are available at runtime but
not exposed to the model by default — they are loaded on demand via
the tool curator when the task requires them.
"""

from __future__ import annotations


TOOL_SPECS = [
    # ── Filesystem ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file (text or PDF). Use offset/limit for large files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file (overwrites). User is asked to confirm.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace the first occurrence of old_text with new_text in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_edit",
            "description": "Apply multiple edits to one file atomically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                            },
                            "required": ["old_text", "new_text"],
                        },
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file. User is asked to confirm.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Recursively list files in a directory, skipping common ignore dirs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to list. Defaults to '.'.",
                    }
                },
                "required": ["directory"],
            },
        },
    },

    # ── Search ──────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_repo",
            "description": "Unified repository search. Auto-selects engine: keyword (ripgrep), symbol (AST), regex, semantic (embeddings), or structural (ast-grep).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query: keyword, symbol name, regex, or natural language."},
                    "path": {"type": "string", "description": "Directory to search. Defaults to '.'."},
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "keyword", "symbol", "regex", "semantic", "structural"],
                        "description": "Search mode. Default 'auto' classifies the query.",
                    },
                    "max_results": {"type": "integer", "description": "Max results (default 50)."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern (e.g. '**/*.py'), sorted by mtime descending.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory to search. Defaults to '.'."},
                    "max_results": {"type": "integer"},
                },
                "required": ["pattern"],
            },
        },
    },

    # ── Execution ───────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command. Use aliases (test, start, dev, install, run) when possible.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                    "run_in_background": {"type": "boolean"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_files",
            "description": "Run language-aware syntax/parse validators on a list of files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "File or files to validate.",
                    },
                },
                "required": ["paths"],
            },
        },
    },

    # ── Execution Plan (Todo) Management ─────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Set the execution plan. Replaces the current plan. Controls task completion — all steps must be completed (or explicitly blocked) before the task finishes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "Step description"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "active", "in_progress", "completed", "blocked", "failed"],
                                    "description": "pending=not started, active/in_progress=in progress, completed=done, blocked=stuck, failed=errored",
                                },
                                "tool_hint": {
                                    "type": "string",
                                    "description": "Expected tool to complete this step (e.g. read_file, edit_file, run_tests, run_command)",
                                },
                                "verification_required": {
                                    "type": "boolean",
                                    "description": "Whether this step needs verification before considered done",
                                },
                            },
                            "required": ["text"],
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "Read the current execution plan (todo list).",
            "parameters": {"type": "object", "properties": {}},
        },
    },

    # ── Git ─────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "git_op",
            "description": "Git operations: status, diff, log, current_branch, branch, commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "diff", "log", "current_branch", "branch", "commit"],
                    },
                    "paths": {
                        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                    },
                    "message": {"type": "string"},
                    "name": {"type": "string"},
                    "base": {"type": "string"},
                    "switch": {"type": "boolean"},
                    "rev": {"type": "string"},
                    "rev_range": {"type": "string"},
                    "staged": {"type": "boolean"},
                    "add_all": {"type": "boolean"},
                    "limit": {"type": "integer"},
                },
                "required": ["action"],
            },
        },
    },

    # ── Recovery ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "checkpoint",
            "description": "Record a milestone in the persisted session transcript.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Short kebab-case milestone name (max 120 chars).",
                    },
                    "summary": {
                        "type": "string",
                        "description": "One paragraph: what was completed and any decisions worth remembering.",
                    },
                    "modified_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of paths touched by this milestone.",
                    },
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_file",
            "description": "Restore a file from a prior automatic snapshot, or list available snapshots.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "index": {
                        "type": "integer",
                        "description": "Snapshot to restore (0 = newest). Default 0.",
                    },
                    "list_only": {
                        "type": "boolean",
                        "description": "If true, return the list of available snapshots without restoring.",
                    },
                },
                "required": ["path"],
            },
        },
    },

    # ── Memory ──────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "add_memory",
            "description": "Persist a fact, convention, or user preference so future sessions remember it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["user", "project", "local"],
                        "description": "Which memory layer to append to.",
                    },
                    "text": {
                        "type": "string",
                        "description": "The fact/convention to remember. Single-line entries become bullets.",
                    },
                },
                "required": ["text"],
            },
        },
    },

    # ── Research ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "save_research_finding",
            "description": "Save a research finding to disk so it never bloats the context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Source URL."},
                    "title": {"type": "string", "description": "Page or article title."},
                    "summary": {
                        "type": "string",
                        "description": "200-500 char summary of the key information found.",
                    },
                    "facts": {
                        "type": "string",
                        "description": "Newline-separated list of key facts extracted.",
                    },
                },
                "required": ["url", "title", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_research_report",
            "description": "Read all research findings accumulated so far (saved via save_research_finding).",
            "parameters": {"type": "object", "properties": {}},
        },
    },

    # ── Browser (core 6) ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "browser_use_task",
            "description": "AI browser agent for multi-step web tasks. Pass full task as natural language.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "llm_provider": {"type": "string"},
                    "model_name": {"type": "string"},
                    "max_steps": {"type": "integer"},
                    "headless": {"type": "boolean"},
                    "use_vision": {"type": "boolean"},
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the user's Chrome browser via OpenCLI.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to."},
                    "session": {
                        "type": "string",
                        "description": "Chrome profile alias (default: 'default').",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Capture a screenshot of the current page and save it as a PNG file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_url",
            "description": "Get the current URL of the active browser tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click any element on the current page by CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element to click."},
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into an element on the current page by CSS selector.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the target element."},
                    "text": {"type": "string", "description": "Text to type into the element."},
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": ["selector", "text"],
            },
        },
    },
]