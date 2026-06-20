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
            "description": "Replace the first occurrence of old_text with new_text  in a file...",
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
            "description": "Apply multiple edits to one file atomically. Each edit is  {old_t...",
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
            "description": "Run a shell command. Use aliases (test, start, dev, install, run)...",
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
            "name": "task_output",
            "description": "Read accumulated output (and status) of a background task started...",
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
            "description": "Find files in .py/.js/.ts/.jsx/.tsx whose contents contain a  key...",
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
            "description": "Search file contents with a regex. Uses ripgrep when available;  ...",
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
            "description": "Rank project source files by semantic similarity to a query  usin...",
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
            "description": "AST-based Python symbol lookup. Returns 'path:line  kind   name' ...",
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
            "description": "List the modules that a Python file imports. Useful  when reasoni...",
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
            "description": "Find files that depend on a Python symbol — either  because they ...",
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
            "description": "Set the agent's working todo list. Replaces the existing list.  U...",
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
            "description": "Call ONLY when in plan mode and you have a complete plan ready.  ...",
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
            "description": "Spawn a subagent to handle a focused task in an isolated  context...",
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
            "description": "Spawn multiple subagents concurrently and aggregate their  summar...",
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
            "description": "Execute a DAG of subagent tasks. Each task has id,  description, ...",
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
            "description": "Record a milestone in the persisted session transcript.  Call thi...",
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
            "description": "Detect the project type from marker files and run its  test suite...",
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
            "description": "Detect the project type and install its dependencies  (pip instal...",
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
            "description": "Triage a failed shell command. Pass the command, its  stdout/stde...",
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
            "description": "Run language-aware syntax / parse validators on a list  of files ...",
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
    {
        "type": "function",
        "function": {
            "name": "self_critique",
            "description": "Pause and have a cheap reviewer model audit your recent  edits fo...",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "File or files to review. Pass the files you  just edited.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "One-line description of what the change was  supposed to achieve ...",
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
            "description": "Restore a file from a prior automatic snapshot, or list  availabl...",
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
                        "description": "If true, return the list of available  snapshots without restorin...",
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
            "description": "Persist a fact, convention, or user preference so future  session...",
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
                        "description": "The fact / convention to remember. Single-line  entries become bu...",
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
            "description": "Open a URL in a headless browser and collect all console  errors,...",
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
    # ------------------------------------------------------------------
    # OpenCLI browser automation tools
    # Requires: npm install -g @jackwener/opencli + Chrome Browser Bridge
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the user's Chrome browser via OpenCLI.  Uses the na...",
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
            "name": "fill_form",
            "description": (
                "Fill a web form on the current page. Pass ``data`` as a "
                "JSON object mapping CSS selectors to values, e.g. "
                "{\"input[name=email]\": \"user@example.com\"}. "
                "Detects the form first, then fills each field with "
                "self-healing retry on selector failure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": (
                            "JSON object: {\"<css-selector>\": \"<value>\", ...}. "
                            "Selector can be any valid CSS selector."
                        ),
                    },
                    "session": {
                        "type": "string",
                        "description": "Chrome profile alias (default: 'default').",
                    },
                },
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upload_file",
            "description": "Upload a local file to a file input on the current page.  Support...",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path to the local file.",
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the file input.  If omitted, tries common file i...",
                    },
                    "session": {
                        "type": "string",
                        "description": "Chrome profile alias (default: 'default').",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_data",
            "description": "Extract text/HTML content from the current page matching  a CSS s...",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector, e.g. 'h2', '.product-title', 'table'.",
                    },
                    "session": {
                        "type": "string",
                        "description": "Chrome profile alias (default: 'default').",
                    },
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_content",
            "description": "Open a URL in a background browser tab for downloading.  Use for ...",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to download from."},
                    "output": {
                        "type": "string",
                        "description": "Local directory to save to (default: '.').",
                    },
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
            "name": "browser_search",
            "description": "Search the web and return results. Uses DuckDuckGo by default  (n...",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query string."},
                    "url": {
                        "type": "string",
                        "description": "Custom URL for site-internal search.  Omit or leave empty to use ...",
                    },
                    "result_selector": {
                        "type": "string",
                        "description": "CSS selector for result elements.  Google results use 'h3' (defau...",
                    },
                    "session": {
                        "type": "string",
                        "description": "Chrome profile alias (default: 'default').",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_login",
            "description": "Navigate to a login page and authenticate with  username/password...",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Login page URL.",
                    },
                    "username": {
                        "type": "string",
                        "description": "Email address or username.",
                    },
                    "password": {
                        "type": "string",
                        "description": "Password.",
                    },
                    "session": {
                        "type": "string",
                        "description": "Chrome profile alias (default: 'default').",
                    },
                },
                "required": ["url", "username", "password"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_setup",
            "description": "Run the full automated OpenCLI setup in one shot:  (1) installs N...",
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
            "name": "browser_setup_verify",
            "description": "Re-run 'opencli doctor' to confirm the Browser Bridge  extension ...",
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
            "name": "browser_submit",
            "description": "Submit the current form or click a specific element.  If ``select...",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector for the element to click.  Omit to auto-find the sub...",
                    },
                    "session": {
                        "type": "string",
                        "description": "Chrome profile alias (default: 'default').",
                    },
                },
                "required": [],
            },
        },
    },
    # ------------------------------------------------------------------
    # Browser primitive tools — full browser automation
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click any element on the current page by CSS selector.  Uses self...",
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
            "description": "Type text into an element on the current page by CSS selector.  U...",
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
    {
        "type": "function",
        "function": {
            "name": "browser_select",
            "description": "Select an option from a dropdown/select element on the current pa...",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the select element."},
                    "value": {"type": "string", "description": "Option value to select."},
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": ["selector", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "Scroll the current page. Default direction is 'down'.  Use to rev...",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up", "left", "right"],
                        "description": "Scroll direction (default: 'down').",
                    },
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Capture a screenshot of the current page and save it as a PNG fil...",
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
            "name": "browser_back",
            "description": "Navigate back one page in the browser history.  Equivalent to cli...",
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
            "name": "browser_eval_js",
            "description": "Execute arbitrary JavaScript in the current page context and  ret...",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "JavaScript expression to evaluate."},
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_keys",
            "description": "Send a keyboard shortcut or key press to the page.  Common combos...",
            "parameters": {
                "type": "object",
                "properties": {
                    "combo": {"type": "string", "description": "Key or keyboard shortcut to send (e.g. 'Escape', 'Enter', 'Control+a')."},
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": ["combo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_tab_list",
            "description": "List all open browser tabs with their target IDs, titles, and URL...",
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
            "name": "browser_tab_new",
            "description": "Open a new browser tab. Optionally navigate to a URL in the new t...",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Optional URL to navigate to in the new tab."},
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_tab_select",
            "description": "Switch to a specific browser tab by its target ID.  Use browser_t...",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "Target tab ID from browser_tab_list."},
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": ["target_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_html",
            "description": "Get the full HTML content of the current page.  Useful for readin...",
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
            "description": "Get the current URL of the active browser tab.  Useful after navi...",
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
            "name": "browser_state",
            "description": "Get the full browser state: current URL, page title, viewport dim...",
            "parameters": {
                "type": "object",
                "properties": {
                    "session": {"type": "string", "description": "Chrome profile alias (default: 'default')."},
                },
                "required": [],
            },
        },
    },
    # ------------------------------------------------------------------
    # Research memory tools — prevent context overflow
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "save_research_finding",
            "description": "Save a research finding to disk so it never bloats the context.  ...",
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
            "description": "Read all research findings accumulated so far (saved via save_res...",
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
            "name": "browser_use_task",
            "description": "AI browser agent for multi-step web tasks. Pass full task as natural language. Replaces manual open_url+click chains.",
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
]
