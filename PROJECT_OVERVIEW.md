# KRYTH - Autonomous AI Coding Agent

## Executive Summary

KRYTH is a sophisticated terminal-based autonomous AI coding agent that can **plan, build, debug, test, and deploy** entire applications with minimal human intervention. It operates as an intelligent REPL (Read-Eval-Print Loop) that orchestrates LLM reasoning with a rich toolkit of over 100+ operations, enabling it to perform complex software engineering tasks autonomously.

**Key Capabilities:**
- Autonomous code generation and project scaffolding
- Intelligent file system operations (read, write, edit, delete with permission prompts)
- Web automation and browser interaction via browser-use integration
- Advanced search and code navigation (semantic, symbol lookup, dependency analysis)
- Shell command execution with safety guards
- Git operations and version control
- Session persistence and context management
- Multi-provider LLM support (OpenAI, Anthropic, Google, Groq, Ollama, etc.)
- Task classification and dynamic tool routing
- Rate limiting and security controls
- Extensible skill system for custom capabilities

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Core Components](#core-components)
3. [External Dependencies](#external-dependencies)
4. [Tool System](#tool-system)
5. [Permission & Security Model](#permission--security-model)
6. [LLM Integration](#llm-integration)
7. [Browser Automation](#browser-automation)
8. [Session & Context Management](#session--context-management)
9. [Configuration System](#configuration-system)
10. [Workflow Examples](#workflow-examples)
11. [Development Setup](#development-setup)

---

## Architecture Overview

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                     User Interface Layer                    │
│  (REPL with Prompt Toolkit, Rich TUI, Event System)       │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                Agent Orchestration Layer                   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           Agent Loop (agent_loop.py)                 │  │
│  │  • Tool dispatch & result handling                   │  │
│  │  • Turn-based reasoning cycle                        │  │
│  │  • Context compression & management                  │  │
│  │  • Error recovery & retry logic                      │  │
│  └──────────────────────────────────────────────────────┘  │
└───────────────────────────┬─────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
┌───────▼───────┐  ┌───────▼───────┐  ┌───────▼───────┐
│  Tools Layer  │  │  LLM Layer    │  │  Permissions  │
│               │  │               │  │               │
│ • File ops    │  │ • Multi-      │  │ • Policy      │
│ • Search      │  │   provider    │  │   evaluation  │
│ • Shell       │  │ • Streaming   │  │ • User prompts│
│ • Browser     │  │ • Planning     │  │ • Caching     │
│ • Git         │  │ • Summarizing │  │               │
│ • Todos       │  │               │  │               │
└───────────────┘  └───────────────┘  └───────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│                Infrastructure Layer                        │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐  │
│  │   Session   │  │   Context   │  │   Model Config  │  │
│  │   Manager   │  │   Builder   │  │   Router        │  │
│  └─────────────┘  └─────────────┘  └──────────────────┘  │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────┐  │
│  │   Repo      │  │   Retriever │  │   Snapshots     │  │
│  │   Index     │  │   (RAG)     │  │   (Rollback)    │  │
│  └─────────────┘  └─────────────┘  └──────────────────┘  │
└───────────────────────────────────────────────────────────┘
```

### Design Principles

1. **Tool-First Architecture**: The agent operates by repeatedly calling tools, capturing results, and asking the LLM what to do next. All capabilities are exposed as callable functions with JSON schemas.

2. **Permission Safety**: Every destructive operation (file writes, deletions, shell commands) goes through a permission system that can allow, deny, or ask the user.

3. **Context Management**: Sophisticated compression and summarization keep token usage under control while preserving important information.

4. **Provider Agnostic**: LLM calls are abstracted through a router that can switch between OpenAI, Anthropic, Google, local models, etc., based on configuration.

5. **Extensible**: New tools, skills, and providers can be added via plugin architecture without modifying core loops.

---

## Core Components

### 1. Agent Loop (`agent/agent_loop.py`)

The heart of the system. Manages the turn-based interaction cycle:

```python
# Simplified flow
while not done:
    # 1. Build context from session, project state, recent messages
    messages = build_messages(user_input, history, context)
    
    # 2. Ask LLM what to do (with tool specs)
    response = llm.ask(messages, tools=TOOL_SPECS)
    
    # 3. Parse tool calls from response
    for tool_call in response.tool_calls:
        # 4. Check permissions
        decision = check_permission(tool_call.name, tool_call.args)
        if decision == "deny":
            result = "Permission denied"
        elif decision == "ask":
            result = ask_user_interactively(tool_call)
        else:  # allow
            # 5. Execute tool
            result = TOOLS[tool_call.name](**tool_call.args)
        
        # 6. Feed result back to LLM
        messages.append(tool_result(result))
    
    # 7. Check for completion, errors, or max turns
    status = evaluate_status(response, turns_used)
```

**Key Features:**
- Streaming responses for real-time output
- Automatic context compression at 40k tokens
- Error recovery with retry logic
- Hook system (PreToolUse, PostToolUse, Stop)
- Turn limit enforcement (default: 100,000)
- Session persistence across restarts

### 2. Tool Registry (`agent/tools/__init__.py`)

Central registry exposing:
- `TOOLS`: dict mapping tool name → callable function
- `TOOL_SPECS`: JSON schemas for LLM function calling
- `READ_ONLY_TOOLS`: safe tools for plan mode
- `SELF_RENDERED_TOOLS`: tools that handle their own UI

**Tool Categories:**

| Category | Tools | Purpose |
|----------|-------|---------|
| File Ops | `read_file`, `write_file`, `edit_file`, `multi_edit`, `delete_file`, `list_files`, `rollback_file` | Complete file system control with atomic writes and rollback |
| Search | `search_code`, `grep`, `glob_files`, `semantic_search`, `lookup_symbol`, `lookup_imports`, `lookup_dependents` | Code navigation and discovery |
| Shell | `run_command`, `task_output` | Execute system commands with timeout and streaming |
| Browser | `open_url`, `fill_form`, `upload_file`, `extract_data`, `browser_search`, `browser_login`, `browser_click`, `browser_type`, `browser_scroll`, `browser_screenshot`, `browser_tab_*`, `browser_eval_js`, `browser_keys` | Full browser automation via OpenCLI/Browser Bridge |
| Git | `git_status`, `git_diff`, `git_log`, `git_commit`, `git_branch`, `git_push`, `git_pull` | Version control operations |
| Planning | `todo_write`, `todo_read`, `exit_plan_mode` | Task planning and tracking |
| Memory | `add_memory` | Persist facts across sessions |
| Meta | `check_browser_errors`, `self_critique`, `checkpoint` | Debugging and quality assurance |

### 3. LLM Layer (`agent/llm.py`, `agent/model_router.py`)

**Abstraction:** Single entry point `ask_llm_stream()` that routes to appropriate provider.

**Providers Supported:**
- OpenAI (GPT-4, GPT-3.5-turbo)
- Anthropic (Claude 3 Opus, Sonnet, Haiku)
- Google (Gemini Pro)
- Groq (Llama 2, Mixtral)
- Ollama (local models)
- LiteLLM (100+ models via unified API)
- DeepSeek, Cerebras, Mistral, Azure, AWS (Bedrock)
- Custom OpenRouter endpoints

**Features:**
- Streaming responses with real-time token counting
- Automatic retry with exponential backoff
- Token budget management
- System prompt injection
- Tool calling (function calling) support
- Multi-turn conversation memory
- Cost tracking (where available)

### 4. Permission System (`agent/permissions.py`)

**Policy Engine:** Evaluates every tool call against user-defined rules.

**Rule Format:** `"tool_name:pattern"` where pattern can include wildcards (`*`).

**Default Policy (from `settings.py`):**
```python
{
  "allow": [
    "read_file:*",
    "list_files:*",
    "search_code:*",
    "grep:*",
    "glob:*",
    "semantic_search:*",
    "lookup_symbol:*",
    "todo_write:*",
    "todo_read:*",
    "task_output:*",
    "exit_plan_mode:*",
  ],
  "ask": [
    "write_file:*",
    "edit_file:*",
    "multi_edit:*",
    "delete_file:*",          # ← Now asks instead of denying
    "run_command:pip install*",
    "run_command:npm install*",
    "run_command:git push*",
    "run_command:docker*",
    "run_command:sudo*",
  ],
  "deny": [
    "run_command:rm -rf*",
    "run_command:shutdown*",
    "run_command:reboot*",
    "run_command:mkfs*",
  ],
  "default": "allow"
}
```

**Decision Flow:**
1. Check explicit `deny` list (highest priority)
2. Check explicit `allow` list
3. If `default == "allow"`: allow; else: deny
4. If tool in `ask` list: prompt user (y=once, a=always, n=never)

**User Interaction:**
```
? Allow delete_file("old_temp.py")? [Y/a/n/d] ?
  Y = allow once
  a = allow for this session
  n = deny this time
  d = deny for this session
```

### 5. Session & Context (`agent/session.py`, `agent/context.py`)

**Session Manager:**
- Persists conversation history across restarts
- Tracks cumulative token usage
- Stores current mode (default, plan, etc.)
- Remembers user profile and preferences
- Auto-saves to `.kryth/sessions/`

**Context Builder:**
- Constructs optimal prompt for LLM
- Injects project map (file tree, git status)
- Adds relevant snippets via RAG (retriever)
- Compresses old messages when token budget exceeded
- Maintains system prompt, tool specs, and recent turns

**Compression Strategy:**
- At 40,000 tokens: summarize old turns into bullet points
- Keep last 12 turns verbatim
- Preserve tool results and user inputs
- Maintain turn count and status markers

### 6. Model Configuration System (`agent/model_config/`)

**Dynamic Model Routing:** Users can define model "personas" in YAML files.

**Example Config (`~/.kryth/models.yaml`):**
```yaml
models:
  fast:
    provider: openai
    model: gpt-3.5-turbo
    temperature: 0.7
  
  creative:
    provider: anthropic
    model: claude-3-opus
    temperature: 1.0
    
  local:
    provider: ollama
    model: llama2
    base_url: http://localhost:11434

router:
  default: fast
  rules:
    - if: "task == 'plan'"
      use: creative
    - if: "task == 'code' and tokens < 4000"
      use: fast
```

**Loader (`model_config/loader.py`):**
- Loads from `~/.kryth/models.yaml` or project `.kryth/models.yaml`
- Merges with built-in defaults
- Validates schema
- Caches config

**Router (`model_config/router.py`):**
- Evaluates rules against current task
- Selects appropriate model
- Falls back to default on errors

**Task Classifier (`task_classifier.py`):**
- Lightweight heuristic classifier
- Categorizes user requests: "code", "plan", "debug", "search", "refactor", "document", "test"
- Used by router to pick models

### 7. Browser Automation (`agent/providers/browser_use_provider.py`, `agent/browser-use/`)

**Two-Layer Architecture:**

1. **KRYTH Tool Layer** (`tools/_opencli.py`): Exposes browser_* functions to agent
2. **Browser-Use Provider** (`providers/browser_use_provider.py`): Adapter to browser-use library
3. **Browser-Use Library** (`browser-use/`): Third-party package with full Playwright integration

**Capabilities:**
- Navigate to URLs
- Click elements (CSS selectors, auto-healing)
- Type text (character-by-character simulation)
- Fill forms (with selector strategies)
- Upload files (drag-drop or input)
- Extract data (CSS selectors, text, HTML)
- Search (DuckDuckGo or site-specific)
- Screenshot capture
- Tab management (list, new, switch)
- JavaScript execution
- Keyboard shortcuts
- Login flows (email/password with heuristics)

**Session Persistence:** Uses Chrome profiles to maintain cookies/local storage across runs.

**Setup:** `browser_setup()` installs OpenCLI bridge extension, verifies connection.

### 8. UI System (`agent/ui/`)

**Rich Terminal Interface using Prompt Toolkit:**

**Components:**
- `renderer.py`: Main output formatter (markdown, code, diffs, JSON)
- `input.py`: Prompt with autocomplete (REPL commands, skills, file paths)
- `hud.py`: Bottom toolbar (model, mode, tokens, depth)
- `panels.py`: Split layout (input, output, side panels)
- `events.py`: Event bus for pub/sub
- `streaming.py`: Real-time LLM output streaming
- `motion.py`: Smooth animations (fade, slide)
- `syntax.py`: Syntax highlighting for code
- `diff_renderer.py`: Unified diff display with colors
- `summarizer.py`: Turn summaries for compact history
- `status_manager.py`: Status indicators (spinner, progress)
- `theme.py`: Color scheme (dark/light, custom palettes)
- `updates.py`: Live updates (like git status)

**Output Types:**
- `ui.console()`: Standard text
- `ui.code()`: Syntax-highlighted code blocks
- `ui.diff()`: Git-style diffs
- `ui.json()`: Pretty-printed JSON
- `ui.markdown()`: Rendered markdown
- `ui.error()`, `ui.warn()`, `ui.success()`: Colored status
- `ui.publish_turn_summary()`: End-of-turn stats

### 9. Retriever & RAG (`agent/retriever.py`, `agent/repo_index.py`)

**Semantic Search:**
- Embeds code files using sentence-transformers
- Stores in ChromaDB (or in-memory)
- Queries with cosine similarity
- Returns top-k relevant snippets

**Symbol Index:**
- AST-based Python symbol extraction
- Fast lookup by name (function, class, variable)
- Tracks definitions and references

**Dependency Graph:**
- Builds import/export relationships
- `lookup_dependents()` finds all files that import a symbol
- Enables safe refactoring

**Repo Index:**
- Incremental updates on file changes
- Invalidates cache when files modified
- Persists to `.kryth/index/`

### 10. Snapshots & Rollback (`agent/snapshots.py`)

**Before every mutating operation:**
- Copies file to `.kryth/snapshots/<timestamp>_<path>`
- Stores metadata (tool, args, timestamp)
- Allows `rollback_file(path, index)` to restore

**Use Cases:**
- Accidental delete recovery
- Bad edit revert
- Experiment rollback

---

## External Dependencies

### Core Runtime (from `pyproject.toml`)

```toml
[dependencies]
python = "^3.10"

# LLM & AI
openai = "^1.0"
anthropic = "^0.18"
google-generativeai = "^0.3"
litellm = "^1.0"  # 100+ models unified
groq = "^0.4"
ollama = "^0.1"

# Browser Automation
browser-use = "^0.1"  # Or local package
playwright = "^1.40"

# Terminal UI
prompt-toolkit = "^3.0"
rich = "^13.0"
pygments = "^2.0"

# Code Analysis
tree-sitter = "^0.20"
astroid = "^3.0"
jedi = "^0.19"

# Vector Search (RAG)
chromadb = "^0.4"
sentence-transformers = "^2.2"

# Utilities
pyyaml = "^6.0"
pydantic = "^2.0"
tenacity = "^8.0"
diskcache = "^5.0"
```

### Optional Integrations

- **Git**: `gitpython` (optional, uses CLI by default)
- **Docker**: `docker` SDK (for container operations)
- **Database**: `sqlalchemy` (for memory persistence)
- **Cloud**: `boto3` (AWS), `google-cloud` (GCP), `azure-identity` (Azure)

### Browser-Use Submodule (`kryth/src/agent/browser-use/`)

This is a **vendored copy** of the `browser-use` library with modifications:

**Key Files:**
- `browser_agent/`: Main agent implementation
- `browser_agent/controller/`: Action controller
- `browser_agent/dom/`: DOM parsing and snapshotting
- `browser_agent/browser/`: Playwright session management
- `browser_agent/llm/`: Multi-provider LLM wrapper
- `browser_agent/skills/`: Pre-built browser skills
- `browser_agent/tools/`: Tool definitions
- `browser_agent/mcp/`: Model Context Protocol server
- `browser_agent/skill_cli/`: CLI for managing skills

**Why Vendored?**
- Tight integration with KRYTH's permission system
- Custom patches for OpenCLI bridge
- Direct access to internal APIs
- Version stability

---

## Tool System Deep Dive

### Tool Definition Pattern

Every tool follows this contract:

```python
def tool_name(arg1: str, arg2: int, optional: bool = False) -> str:
    """
    Tool description for LLM.
    
    Args:
        arg1: Description
        arg2: Description
        optional: Description
    
    Returns:
        Result string (success or [ERROR CODE] message)
    """
    # 1. Validate inputs
    if not arg1:
        return err("BAD_ARGS", "arg1 required")
    
    # 2. Check permissions (handled by agent_loop, not here)
    #    Tools assume permission already granted
    
    # 3. Execute operation
    try:
        result = do_something(arg1, arg2)
        return f"Success: {result}"
    except Exception as e:
        return err("EXEC_FAILED", "operation failed", str(e))
```

**Error Format:**
```
[ERROR <CODE>] <short message>
<optional details>
```

Common codes: `NOT_FOUND`, `BAD_ARGS`, `AMBIGUOUS`, `EXEC_FAILED`, `NON_ZERO_EXIT`, `TIMEOUT`, `PERMISSION_DENIED`

### Tool Registration

In `agent/tools/__init__.py`:

```python
from agent.tools._file_ops import read_file, write_file, ...
from agent.tools._search import grep, search_code, ...
from agent.tools._shell import run_command, task_output
from agent.tools._browser import check_browser_errors
from agent.tools._opencli import (
    open_url, fill_form, upload_file, extract_data,
    browser_search, browser_login, browser_click, ...
)
from agent.tools._git import git_status, git_diff, git_commit, ...
from agent.tools._todos import todo_write, todo_read
from agent.tools._plan import exit_plan_mode
from agent.tools._memory import add_memory
from agent.tools._results import err

TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "delete_file": delete_file,
    "grep": grep,
    "run_command": run_command,
    "open_url": open_url,
    # ... 100+ more
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents with optional offset/limit",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "offset": {"type": "integer", "description": "Start line (1-indexed)"},
                    "limit": {"type": "integer", "description": "Max lines to return"},
                },
                "required": ["path"]
            }
        }
    },
    # ... matching specs for each tool
]
```

**Sanity Check:** At import, verifies `TOOLS.keys() == {spec['function']['name'] for spec in TOOL_SPECS}`. Mismatch raises RuntimeError.

---

## Permission & Security Model

### Three-Tier System

1. **Global Defaults** (`settings.py`): Built-in safe defaults
2. **User Overrides** (`.kryth/settings.json`): Project-specific rules
3. **Session Memory** (prompt-time decisions): "a" (always) and "d" (never) choices

### Rule Matching

```python
def check_permission(tool: str, args: dict) -> str:
    """
    Returns: "allow", "deny", or "ask"
    """
    # Load settings (defaults + user overrides)
    settings = load_settings()
    perms = settings["permissions"]
    
    # Build tool signature: "tool_name:arg1_value" or "tool_name:*"
    # For pattern matching, we use wildcard expansion
    signature = f"{tool}:*"  # Generic pattern
    
    # If args contain sensitive values (like paths), we can create
    # more specific patterns, but wildcard is typical
    
    # 1. Check deny list
    for pattern in perms["deny"]:
        if fnmatch(signature, pattern):
            return "deny"
    
    # 2. Check ask list
    for pattern in perms["ask"]:
        if fnmatch(signature, pattern):
            return "ask"
    
    # 3. Check allow list
    for pattern in perms["allow"]:
        if fnmatch(signature, pattern):
            return "allow"
    
    # 4. Default
    return perms["default"]
```

### Session Memory

During a session, user choices are cached:

```python
session.permission_cache = {
    ("delete_file", "*.py"): "allow",  # User said "a" (always)
    ("run_command", "rm -rf*"): "deny",  # User said "d" (never)
}
```

Cache cleared on session end (unless persisted to disk in future).

### Safety Defaults

**Always Denied (hard-coded in some tools):**
- Deleting directories (only files allowed)
- Running `rm -rf /` or other catastrophic commands
- Accessing outside project root (sandbox)
- Modifying `.kryth/` internals (snapshots, sessions)

**Always Ask (default):**
- File writes (overwrites)
- File deletions
- Shell commands (except read-only like `ls`, `cat`)
- Git push (force pushes always denied)
- Browser navigation (can be changed)

**Always Allowed:**
- Read operations
- Search operations
- Todo management
- Plan mode exit

---

## LLM Integration Details

### Provider Interface

All providers implement:

```python
class LLMProvider(Protocol):
    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[ToolSpec]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        stream: bool = False,
    ) -> Union[Response, Iterator[Chunk]]:
        ...
```

**Message Format:**
```python
{
    "role": "system" | "user" | "assistant" | "tool",
    "content": "text content",
    "tool_calls": [...],  # from assistant
    "tool_call_id": "...",  # from tool result
}
```

### Streaming

Uses server-sent events or chunked responses:

```python
for chunk in llm.chat(messages, stream=True):
    if chunk.content:
        ui.stream_text(chunk.content)
    if chunk.tool_calls:
        ui.muted(f"→ invoking {len(chunk.tool_calls)} tools")
```

### Tool Calling

Two modes:

1. **Native function calling** (OpenAI, Anthropic, etc.): LLM returns structured `tool_calls` array with `name`, `arguments`, `id`.

2. **Text parsing** (older models): Parse ````json\n{"name": "...", "arguments": {...}}```` from response.

Agent loop handles both.

### Cost Tracking

When provider supports it:
- Count input/output tokens
- Multiply by model pricing (from `model_pricing.json`)
- Accumulate in session: `session.cumulative_cost`
- Display in turn summary

---

## Browser Automation Deep Dive

### Architecture

```
KRYTH Tool (open_url, click, type)
    ↓
BrowserUseProvider (adapter)
    ↓
BrowserUse Library (browser_use package)
    ↓
Playwright (puppeteer-like)
    ↓
Chrome/Chromium via OpenCLI Bridge
```

### OpenCLI Bridge

**What:** Chrome extension + local server that allows programmatic browser control.

**Installation:** `browser_setup()` does:
1. Check Node.js >= 20
2. `npm install -g @jackwener/opencli`
3. Open Chrome Web Store page for "Browser Bridge"
4. User clicks "Add to Chrome"
5. Run `opencli doctor` to verify

**Connection:** WebSocket to `ws://localhost:9222` (DevTools protocol).

### Browser-Use Features

**Smart Selectors:**
- CSS selectors with fallbacks
- Text-based: `"Submit"` → finds button with that text
- ARIA labels: `[aria-label="Search"]`
- XPath support
- Auto-healing: if selector fails, tries alternatives

**DOM Snapshot:**
- Extracts interactive elements (buttons, inputs, links)
- Builds accessibility tree
- Provides context to LLM about page structure
- Cached for performance

**Skills:**
Pre-built workflows:
- `search_google()`: Navigate, type, submit, extract results
- `login_gmail()`: Fill email, password, 2FA handling
- `scrape_table()`: Extract HTML tables to CSV
- `download_file()`: Click download links, wait for completion

**Video Recording:**
- Optional: record entire session as MP4
- Useful for debugging or demonstrations

### Security

- Runs in isolated Chrome profile (separate from user's main browser)
- No access to saved passwords unless explicitly used
- Can be run headless (no UI)
- Rate limiting prevents abuse
- Session can be killed if hung

---

## Session & Context Management

### Session Lifecycle

```python
# On startup
session = get_session()  # Load from .kryth/sessions/<id>.json or create new

# During agent loop
session.messages.append(user_input)
session.cumulative_in_tokens += count_tokens(user_input)
session.depth += 1  # Nesting level (subagents)

# On tool execution
session.tool_uses[tool_name] += 1

# On turn end
session.cumulative_out_tokens += output_tokens
session.turns += 1
save_session(session)  # Async write

# On shutdown (Ctrl+C)
session.save()
```

### Session File Format

```json
{
  "id": "sess_abc123",
  "created_at": "2025-06-06T10:30:00",
  "last_active": "2025-06-06T14:45:00",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "content": "...", "tool_call_id": "..."}
  ],
  "cumulative_in_tokens": 125000,
  "cumulative_out_tokens": 89000,
  "cumulative_cost": 2.34,
  "mode": "default",
  "profile": "balanced",
  "depth": 0,
  "todos": [...],
  "permission_cache": {...}
}
```

### Context Compression

When token count exceeds `COMPACT_AT_TOKENS` (40,000):

1. **Summarize old turns:** Use LLM to create bullet-point summary of conversation before cutoff
2. **Keep recent turns:** Last `KEEP_RECENT_AFTER_COMPACT` (12) turns kept verbatim
3. **Replace old messages:** With single summary message from "system" role
4. **Preserve tool results:** Important data not lost

**Example:**
```
Before: 100 turns, 45k tokens
After:
  - System: "Summary: User wants to build a Flask API. Discussed endpoints, DB schema. Implemented models and routes."
  - Turns 89-100: (verbatim, 12 turns)
  → Total: 12k tokens
```

---

## Configuration System

### Settings Hierarchy

1. **Built-in Defaults** (`agent/settings.py:DEFAULTS`)
2. **User Global** (`~/.kryth/settings.json`)
3. **Project Local** (`<project>/.kryth/settings.json`)
4. **Environment Variables** (`KRYTH_*`)

### Settings File Structure

```json
{
  "permissions": {
    "allow": ["read_file:*", "list_files:*"],
    "ask": ["write_file:*", "delete_file:*"],
    "deny": ["run_command:rm -rf*"],
    "default": "allow"
  },
  "models": {
    "default": "gpt-4",
    "fallbacks": ["gpt-3.5-turbo", "claude-3-haiku"]
  },
  "ui": {
    "theme": "dark",
    "show_tool_calls": true,
    "compact_mode": false
  },
  "agent": {
    "max_tool_turns": 100000,
    "compress_at_tokens": 40000,
    "auto_retry": 3
  },
  "hooks": {
    "PreToolUse": ["log_tool_use"],
    "PostToolUse": ["update_index"],
    "Stop": ["save_session", "print_summary"]
  },
  "skills_dir": ".kryth/skills"
}
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `KRYTH_MODEL` | Override default model |
| `KRYTH_API_KEY` | API key for provider |
| `KRYTH_BASE_URL` | Custom endpoint (for local models) |
| `KRYTH_MAX_TOOL_TURNS` | Turn limit override |
| `KRYTH_VALIDATE_MODELS` | Run health check on startup |
| `KRYTH_DEBUG` | Enable debug logging |
| `KRYTH_NO_COLOR` | Disable colors in output |

---

## Workflow Examples

### Example 1: Build a Flask API

```
User: "Create a Flask REST API with User model, auth, and 3 endpoints"

Agent Loop:
1. LLM decides: need to scaffold project
2. Calls: write_file("app.py", "from flask import Flask...")
3. Calls: write_file("models.py", "from flask_sqlalchemy import SQLAlchemy...")
4. Calls: write_file("requirements.txt", "Flask==2.0...")
5. Asks user: "Run pip install? [Y/a/n]"
6. User: "a" (always allow for this session)
7. Calls: run_command("pip install -r requirements.txt")
8. Calls: run_command("flask db init")
9. Calls: write_file("migrations/...", migration script)
10. LLM: "Done. API ready at http://localhost:5000"
```

### Example 2: Debug a Bug

```
User: "Why is my function returning None?"

Agent:
1. Calls: grep("def my_function") → finds file
2. Calls: read_file("utils.py") → sees code
3. Calls: grep("my_function(") → finds call sites
4. Analyzes: "Caller doesn't check return value"
5. Calls: edit_file("utils.py", old="return result", new="return result or default")
6. Suggests: "Fixed. Also add unit test?"
7. User: "Yes"
8. Calls: write_file("test_utils.py", pytest code)
9. Calls: run_command("pytest test_utils.py -v")
10. Reports: "All tests pass"
```

### Example 3: Web Research

```
User: "Find latest React best practices and summarize"

Agent:
1. Calls: browser_search("React best practices 2024")
2. Calls: open_url(first_result)
3. Calls: extract_data("article") → gets content
4. Calls: save_research_finding(url, title, summary, facts)
5. Repeats for top 5 results
6. Calls: get_research_report() → gets all findings
7. Synthesizes: "Here are 2024 React best practices: ..."
```

---

## Development Setup

### Prerequisites

- Python 3.10+
- Git
- Node.js 20+ (for browser automation)
- Chrome browser (for browser automation)

### Installation

```bash
# Clone
git clone https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent.git
cd KRYTH-Autonomous-AI-Coding-Agent

# Install (editable)
pip install -e kryth

# Or with dev dependencies
pip install -e "kryth[dev]"

# Initialize config
kryth --validate  # Check model config
```

### Configuration

1. **API Keys:** Set in environment or `~/.kryth/config.yaml`:
   ```yaml
   openai_api_key: "sk-..."
   anthropic_api_key: "sk-..."
   ```

2. **Model Config (optional):** `~/.kryth/models.yaml` for custom routing.

3. **Permissions (optional):** `.kryth/settings.json` in project.

### Running

```bash
# Start REPL
kryth
# or
ai-coder
# or
xerocodeai

# With flags
kryth --profile safe  # Use safe profile (more prompts)
kryth --validate      # Just run health check
```

### Testing

```bash
pytest tests/
# or
python -m pytest tests/test_runtime_core.py -v
```

### Project Structure

```
KRYTH-Autonomous-AI-Coding-Agent/
├── kryth/                      # Main package
│   ├── src/
│   │   ├── kryth/             # CLI entry point
│   │   │   ├── main.py        # Bootstrapper
│   │   │   └── _repl_main.py  # REPL loop
│   │   └── agent/             # Core agent framework
│   │       ├── agent_loop.py      # Orchestration
│   │       ├── llm.py             # LLM abstraction
│   │       ├── permissions.py     # Security
│   │       ├── session.py         # State persistence
│   │       ├── context.py         # Prompt building
│   │       ├── tools/             # 100+ tools
│   │       │   ├── __init__.py    # Registry
│   │       │   ├── _file_ops.py   # File operations
│   │       │   ├── _search.py     # Code search
│   │       │   ├── _shell.py      # Shell commands
│   │       │   ├── _opencli.py    # Browser automation
│   │       │   ├── _git.py        # Git operations
│   │       │   ├── _todos.py      # Task tracking
│   │       │   ├── _plan.py       # Plan mode
│   │       │   ├── _memory.py     # Memory storage
│   │       │   ├── _results.py    # Error formatting
│   │       │   └── ...
│   │       ├── ui/                # Terminal UI
│   │       │   ├── renderer.py
│   │       │   ├── input.py
│   │       │   ├── events.py
│   │       │   └── ...
│   │       ├── model_config/      # Dynamic model routing
│   │       │   ├── loader.py
│   │       │   ├── router.py
│   │       │   ├── schema.py
│   │       │   └── validator.py
│   │       ├── providers/         # LLM provider adapters
│   │       │   ├── openai.py
│   │       │   ├── anthropic.py
│   │       │   ├── google.py
│   │       │   ├── groq.py
│   │       │   ├── ollama.py
│   │       │   ├── litellm.py
│   │       │   ├── browser_use_provider.py
│   │       │   └── ...
│   │       ├── browser-use/       # Vendored browser-use library
│   │       │   ├── browser_agent/
│   │       │   ├── pyproject.toml
│   │       │   └── ...
│   │       ├── executor/          # Action execution engine
│   │       ├── ecosystem/         # Skill/plugin system
│   │       ├── memory/            # RAG and indexing
│   │       ├── retriever.py       # Semantic search
│   │       ├── repo_index.py      # File indexing
│   │       ├── snapshots.py       # Rollback system
│   │       ├── hooks.py           # Event hooks
│   │       ├── profiles.py        # Permission profiles
│   │       ├── settings.py        # Config loader
│   │       ├── env.py             # Environment vars
│   │       ├── prompts.py         # System prompts
│   │       ├── task_classifier.py # Task categorization
│   │       └── ...
│   └── ...
├── tests/
│   ├── conftest.py
│   └── test_runtime_core.py
├── .github/
│   └── workflows/
├── README.md
├── pyproject.toml
├── .gitignore
└── LICENSE
```

---

## Key Technical Decisions

### Why Tool Calling vs. Text Parsing?

**Tool calling** (structured function schemas) is more reliable than parsing free-text commands. Modern LLMs natively support it, and it eliminates ambiguity.

### Why Permission Prompts?

Safety. Autonomous agents can be destructive. Asking before writes/deletes prevents data loss while still enabling productivity. Users can "always allow" for trusted operations.

### Why Separate Browser-Use?

Browser automation is complex. Using a dedicated library (`browser-use`) avoids reinventing the wheel. Vendoring ensures version stability and allows deep integration.

### Why Session Persistence?

Long-running projects span multiple days. Saving conversation history and token counts allows resuming where you left off.

### Why Context Compression?

LLMs have token limits (128k-200k typical). Without compression, long projects would hit limits. Summarization preserves context while staying within budget.

### Why Multi-Provider Support?

No single provider is best for all tasks. Some are fast, some are smart, some are cheap. Routing based on task type optimizes cost/quality.

---

## Extending KRYTH

### Adding a New Tool

1. Define function in `agent/tools/_my_tool.py`:
   ```python
   def my_tool(param: str) -> str:
       """Do something useful."""
       # Implementation
       return "Result"
   ```

2. Export in `agent/tools/__init__.py`:
   ```python
   from agent.tools._my_tool import my_tool
   TOOLS["my_tool"] = my_tool
   ```

3. Add spec to `TOOL_SPECS` (or auto-generated if using decorator).

4. (Optional) Add permission rule to defaults in `settings.py`.

### Adding a New LLM Provider

1. Create `agent/providers/my_provider.py`:
   ```python
   from agent.llm import LLMProvider
   
   class MyProvider(LLMProvider):
       def chat(self, messages, tools=None, **kwargs):
           # Convert messages to provider format
           # Call API
           # Convert response to standard format
           return response
   ```

2. Register in `agent/model_config/providers.py`.

3. Add to `SUPPORTED_PROVIDERS` list.

4. Update docs.

### Adding a Skill

Skills are higher-level workflows composed of multiple tool calls.

1. Create `agent/skills/my_skill.py`:
   ```python
   from agent.skills import skill
   
   @skill("my-skill", "Does something complex")
   def my_skill_handler(args: dict) -> str:
       # Call tools via agent loop (subagent)
       return result
   ```

2. Skills auto-discovered from `skills_dir`.

---

## Performance Considerations

- **Token Efficiency:** Context compression keeps long sessions viable
- **Caching:** Retriever caches embeddings; repo index caches file metadata
- **Lazy Loading:** Heavy modules (retriever, repo_index) imported only when used
- **Streaming:** LLM responses streamed token-by-token, not buffered
- **Parallelism:** Some operations (indexing, compression) can run in background

---

## Known Limitations

1. **Browser automation** requires Chrome and OpenCLI bridge (not headless by default)
2. **Semantic search** needs embedding model download (~100MB) on first run
3. **Large repos** (>10k files) may need index tuning
4. **Token limits** still apply; very long projects need manual summarization
5. **No built-in testing framework** (relies on user's existing tests)
6. **Windows support** is good but some tools assume Unix (grep, find)

---

## Future Roadmap

- [ ] Built-in test runner integration (pytest, jest, go test)
- [ ] Multi-agent collaboration (spawn subagents for parallel work)
- [ ] Visual diff editor integration
- [ ] Cloud sync for sessions
- [ ] Plugin marketplace for community skills
- [ ] Voice input/output
- [ ] IDE plugin (VS Code, JetBrains)
- [ ] Docker container sandboxing
- [ ] Automatic PR creation from agent work
- [ ] Team knowledge base integration

---

## License

MIT License - see LICENSE file.

---

## Credits

Built with:
- OpenAI, Anthropic, Google, Groq, Ollama (LLM providers)
- browser-use library (browser automation)
- Prompt Toolkit, Rich (terminal UI)
- ChromaDB (vector search)
- Playwright (browser control)
- And many more open-source libraries

---

**Last Updated:** 2025-06-06
**Version:** 0.1.0-dev
**Maintainer:** navadeep0508