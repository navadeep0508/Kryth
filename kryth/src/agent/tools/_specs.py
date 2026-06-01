"""JSON-schema specs for every tool, exposed to the model via the
chat-completions ``tools=`` parameter.

Kept in sync with the registry in ``__init__.py``. The package-level
__init__ asserts that the name set in this list matches TOOLS, so a
drifted spec fails at import time rather than at runtime.
"""

from __future__ import annotations


TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file. Output has 1-indexed line numbers "
                "(tab-separated). Use offset/limit for large files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {
                        "type": "integer",
                        "description": "0-indexed line number to start at.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max number of lines to return.",
                    },
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
            "description": (
                "Replace the first occurrence of old_text with new_text "
                "in a file. Prefer this over write_file for changes."
            ),
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
            "description": (
                "Apply multiple edits to one file atomically. Each edit is "
                "{old_text, new_text}, applied in order. If any old_text is "
                "missing, no changes are written."
            ),
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
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command. Use aliases (test, start, dev, install, run) "
                "when possible. Set run_in_background=true for long-running processes; "
                "retrieve their output with task_output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds before killing the command (1-600). Default 15.",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "If true, spawn the command and return a task id immediately.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_output",
            "description": (
                "Read accumulated output (and status) of a background task started "
                "by run_command. Pass kill=true to terminate it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "kill": {"type": "boolean"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Find files in .py/.js/.ts/.jsx/.tsx whose contents contain a "
                "keyword (case-insensitive substring). Prefer 'grep' for regex / "
                "line-level results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "directory": {"type": "string"},
                },
                "required": ["keyword", "directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Search file contents with a regex. Uses ripgrep when available; "
                "falls back to a pure-Python walk. Returns matching file paths by default."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "File or directory to search. Defaults to '.'.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "fnmatch pattern to filter file names, e.g. '*.py'.",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["files_with_matches", "content", "count"],
                    },
                    "case_insensitive": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                "required": ["pattern"],
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
                    "path": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": (
                "Rank project source files by semantic similarity to a query "
                "using sentence embeddings. Use for vague intent like "
                "'where is auth handled?' when grep keywords are unknown. "
                "Returns 'path<TAB>score' lines. Falls back to a hint when "
                "the embedding model is unavailable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {
                        "type": "integer",
                        "description": "Max results (1-25). Default 5.",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Project root. Defaults to '.'.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_symbol",
            "description": (
                "AST-based Python symbol lookup. Returns 'path:line  kind  "
                "name' lines for every match. Use this — not grep — when "
                "you need to find where a Python function/class/method is "
                "DEFINED. Falls back to '(no matches)' when no Python "
                "definition matches; supports short-name matching (e.g. "
                "'foo' finds 'Bar.foo')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "directory": {"type": "string", "description": "Project root. Defaults to '.'."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_imports",
            "description": (
                "List the modules that a Python file imports. Useful "
                "when reasoning about what a file depends on before you "
                "edit it. Output is one module per line, sorted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "directory": {"type": "string", "description": "Project root. Defaults to '.'."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_dependents",
            "description": (
                "Find files that depend on a Python symbol — either "
                "because they import the symbol's module, or because "
                "they call ``name``. Use this BEFORE renaming or "
                "deleting a function/class to find every consumer that "
                "needs the matching update. Returns two lists (import "
                "edges + call edges); they may overlap."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "directory": {"type": "string", "description": "Project root. Defaults to '.'."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": (
                "Set the agent's working todo list. Replaces the existing list. "
                "Use to plan multi-step work and track progress."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
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
            "description": "Read the current todo list.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exit_plan_mode",
            "description": (
                "Call ONLY when in plan mode and you have a complete plan ready. "
                "Presents the plan to the user; on approval the session leaves "
                "plan mode and you may make changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": "The implementation plan, in plain prose or markdown.",
                    },
                },
                "required": ["plan"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": (
                "Spawn a subagent to handle a focused task in an isolated "
                "context window. Returns only the subagent's final summary. "
                "Good for parallel research or scoped multi-step work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Short label for the subagent's task.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Full self-contained instructions for the subagent.",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": "Tool turns before the subagent gives up. Default 8.",
                    },
                },
                "required": ["description", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_agents_parallel",
            "description": (
                "Spawn multiple subagents concurrently and aggregate their "
                "summaries. Use for fan-out READ work (audit several "
                "modules in parallel, compare codebases, gather "
                "independent context). Do NOT use for concurrent writes "
                "to the same files — workers share cwd. Results return "
                "in input order regardless of which subagent finishes "
                "first. Default concurrency 3, hard cap 4."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "prompt": {"type": "string"},
                                "max_turns": {"type": "integer"},
                            },
                            "required": ["description", "prompt"],
                        },
                    },
                    "max_concurrency": {
                        "type": "integer",
                        "description": "Concurrent workers. Default 3, max 4.",
                    },
                },
                "required": ["tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_task_graph",
            "description": (
                "Execute a DAG of subagent tasks. Each task has id, "
                "description, prompt, and optional depends_on list. "
                "Independent siblings run in parallel; downstream tasks "
                "wait for their upstreams. Prompts may reference "
                "upstream output via {{upstream_id}} placeholders — the "
                "runner substitutes the upstream's final summary before "
                "dispatch. Returns a single string with one block per "
                "task in input order. Use this for build-then-test, "
                "multi-area refactors, or any plan where steps have a "
                "real ordering. Cycles and unknown deps fail fast."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "description": {"type": "string"},
                                "prompt": {"type": "string"},
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "max_turns": {"type": "integer"},
                            },
                            "required": ["id", "description", "prompt"],
                        },
                    },
                    "max_concurrency": {
                        "type": "integer",
                        "description": "Sibling fan-out width. Default 3, max 4.",
                    },
                },
                "required": ["tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "checkpoint",
            "description": (
                "Record a milestone in the persisted session transcript. "
                "Call this after finishing a coherent unit of work — a "
                "phase, a feature, an end-to-end fix — NOT after every "
                "edit. The operator sees these on /resume so they can "
                "tell at a glance how far you got. Provide a short label "
                "(e.g. 'add-checkpoint-tool') and a 1-2 sentence summary."
            ),
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
            "name": "run_tests",
            "description": (
                "Detect the project type from marker files and run its "
                "test suite (pytest, npm/pnpm/yarn test, cargo test, "
                "go test). Returns a headline (counts when extractable) "
                "plus the tail of output. Use this instead of guessing "
                "the right command via run_command — the wrapper picks "
                "the right package manager from lockfiles."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Project root. Defaults to '.'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_install",
            "description": (
                "Detect the project type and install its dependencies "
                "(pip install -e . / -r requirements.txt, npm/pnpm/yarn "
                "install, cargo build, go mod tidy). Permission gate "
                "should treat this as mutating since it modifies the "
                "environment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Project root. Defaults to '.'."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_error",
            "description": (
                "Triage a failed shell command. Pass the command, its "
                "stdout/stderr tails, the exit code, and optionally a "
                "context_hint describing what you were trying to do. A "
                "small reviewer model returns CAUSE / FIX / CONFIDENCE "
                "lines. Call this when run_command fails and the "
                "failure isn't obvious — saves you from burning main-"
                "model context on long error traces."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "stdout": {"type": "string"},
                    "stderr": {"type": "string"},
                    "exit_code": {"type": "integer"},
                    "context_hint": {
                        "type": "string",
                        "description": "One-liner: what were you trying to accomplish.",
                    },
                },
                "required": ["command", "exit_code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify_files",
            "description": (
                "Run language-aware syntax / parse validators on a list "
                "of files (or a single path) AFTER you've edited them. "
                "Checks: .py (py_compile), .json, .yaml/.yml, .toml. "
                "Files with no matching validator are skipped. Returns "
                "either a 'validation: N issue(s)' report or a clean "
                "confirmation. Cheap and deterministic — no network."
            ),
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
    {
        "type": "function",
        "function": {
            "name": "git_op",
            "description": (
                "Structured git operations. Prefer this over run_command "
                "for git workflows — the output is more parseable and the "
                "permission layer can grant read-only git actions "
                "without granting full shell. Actions: 'status' (porcelain "
                "summary), 'diff' (unified diff; pass staged=true or rev "
                "to narrow), 'log' (oneline history, default last 15), "
                "'current_branch', 'branch' (no name = list; name = "
                "create/switch), 'commit' (stage paths + commit with "
                "message; pass add_all=true to stage everything). "
                "Mutating actions (branch, commit) still go through the "
                "permission system."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "status", "diff", "log",
                            "current_branch", "branch", "commit",
                        ],
                    },
                    "paths": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": (
                            "For 'diff': narrow to these paths. For "
                            "'commit': stage these specific paths."
                        ),
                    },
                    "message": {
                        "type": "string",
                        "description": "Commit message (required for action='commit').",
                    },
                    "name": {
                        "type": "string",
                        "description": "Branch name (action='branch').",
                    },
                    "base": {
                        "type": "string",
                        "description": (
                            "Starting point for a new branch (commit-ish). "
                            "Default: current HEAD."
                        ),
                    },
                    "switch": {
                        "type": "boolean",
                        "description": (
                            "When creating a branch, also switch to it. "
                            "Default true."
                        ),
                    },
                    "rev": {
                        "type": "string",
                        "description": "Diff against this ref (e.g. 'main', 'HEAD~3').",
                    },
                    "rev_range": {
                        "type": "string",
                        "description": "Restrict log output to this range (e.g. 'main..HEAD').",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "diff: show staged changes only.",
                    },
                    "add_all": {
                        "type": "boolean",
                        "description": "commit: run 'git add -A' before committing.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "log: max commits to return. Default 15.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_critique",
            "description": (
                "Pause and have a cheap reviewer model audit your recent "
                "edits for correctness bugs, missed imports, broken "
                "assumptions, and regressions. Call this AFTER you finish "
                "a feature / fix / refactor — not after every micro-edit. "
                "Each path is diffed against the snapshot taken before "
                "the last write (every write_file / edit_file / "
                "multi_edit creates one), and the combined diff is "
                "graded. Provide 'intent' so the reviewer knows what the "
                "change was meant to accomplish."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": (
                            "File or files to review. Pass the files you "
                            "just edited."
                        ),
                    },
                    "intent": {
                        "type": "string",
                        "description": (
                            "One-line description of what the change was "
                            "supposed to achieve (e.g. 'add JWT refresh "
                            "endpoint')."
                        ),
                    },
                },
                "required": ["paths"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rollback_file",
            "description": (
                "Restore a file from a prior automatic snapshot, or list "
                "available snapshots without restoring. Every write_file / "
                "edit_file / multi_edit / delete_file creates a snapshot "
                "first, so this is how you undo a bad edit. index=0 is the "
                "most recent backup. Pass list_only=true to inspect."
            ),
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
                        "description": (
                            "If true, return the list of available "
                            "snapshots without restoring anything."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_memory",
            "description": (
                "Persist a fact, convention, or user preference so future "
                "sessions inherit it. Use sparingly — only for things "
                "worth remembering across runs (project conventions, "
                "infrastructure quirks, explicit user instructions). "
                "Scopes: 'user' = follows the user across all projects "
                "(~/.kryth/MEMORY.md); 'project' = team-shared "
                "(<project>/AGENTS.md); 'local' = subdir-specific "
                "(<cwd>/AGENTS.md). Defaults to 'project'."
            ),
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
                        "description": (
                            "The fact / convention to remember. Single-line "
                            "entries become bullets; multi-line entries "
                            "become headed sections."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_browser_errors",
            "description": (
                "Open a URL in a headless browser and collect all console "
                "errors, warnings, and failed network requests. Use this "
                "after starting a dev server to catch JavaScript runtime "
                "errors, import failures, and 404s that don't appear in "
                "the terminal. Essential for debugging full-stack apps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to check, e.g. http://localhost:5173",
                    },
                    "wait_seconds": {
                        "type": "integer",
                        "description": "Seconds to wait for JS to run (default 5).",
                    },
                },
                "required": ["url"],
            },
        },
    },
]
