# Configuration

Complete reference for configuring KRYTH.

## Table of Contents

- [Environment Variables](#environment-variables)
- [Config File](#config-file)
- [Permission Profiles](#permission-profiles)
- [Model Routing](#model-routing)
- [Memory System](#memory-system)
- [Logging](#logging)
- [Advanced Settings](#advanced-settings)

## Environment Variables

Set these in your shell or `.env` file.

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `OPENAI_API_KEY` | API key for OpenAI or compatible service | `sk-...` |

### Optional - API Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_BASE_URL` | API endpoint URL | `https://api.openai.com/v1` |
| `MAIN_MODEL` | Model for general tasks | `gpt-4-turbo-preview` |
| `PLANNER_MODEL` | Model for planning | `gpt-4-turbo-preview` |
| `SUMMARIZER_MODEL` | Model for summarization | `gpt-4-turbo-preview` |

### Optional - Provider Keys

For alternative providers:

| Variable | Provider |
|----------|----------|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) |
| `GEMINI_API_KEY` | Google Gemini |
| `GROQ_API_KEY` | Groq |
| `TOGETHER_API_KEY` | Together AI |
| `OLLAMA_BASE_URL` | Local Ollama |

### Optional - Behavior

| Variable | Description | Default |
|----------|-------------|---------|
| `AICODER_PROFILE` | Permission profile | `default` |
| `AICODER_NO_RESUME` | Disable auto-resume | `false` |
| `AICODER_ASSUME_YES` | Auto-approve all actions | `false` |

### Optional - Bridge Mode

| Variable | Description | Default |
|----------|-------------|---------|
| `BRIDGE_PORT` | Port for bridge server | `8765` |
| `BRIDGE_PROVIDER` | Default provider | `gemini` |

### Optional - Logging

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_LEVEL` | Logging level | `INFO` |
| `LOG_FILE` | Log file path | `~/.kryth/kryth.log` |

Levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`

### Optional - Performance

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_TOKENS` | Maximum tokens per request | `4096` |
| `REQUEST_TIMEOUT` | API timeout in seconds | `60` |

### Optional - Development

| Variable | Description | Default |
|----------|-------------|---------|
| `DEBUG` | Enable debug mode | `false` |
| `PYTHONPATH` | Additional Python paths | - |

## Config File

KRYTH stores persistent configuration in a JSON file.

### Location

- Linux/macOS: `~/.ai-coder/config.json`
- Windows: `%APPDATA%\ai-coder\config.json`

### Format

```json
{
  "openai_api_key": "sk-...",
  "base_url": "https://api.openai.com/v1",
  "main_model": "gpt-4-turbo-preview",
  "planner_model": "gpt-4-turbo-preview",
  "summarizer_model": "gpt-4-turbo-preview",
  "profile": "default",
  "log_level": "INFO",
  "max_tokens": 4096,
  "request_timeout": 60,
  "auto_resume": true,
  "assume_yes": false
}
```

### Editing Config

Use the TUI:

```
/config
```

Or edit the file directly with any text editor.

## Permission Profiles

Profiles control how autonomous KRYTH is when executing actions.

### Available Profiles

#### `default` (Recommended)

Balanced approach with reasonable safety checks.

- **Default**: `ask` - Prompt for confirmation
- **Allow**: Safe read operations, basic file edits
- **Ask**: File modifications, shell commands, git operations
- **Deny**: System-altering operations, dangerous commands

#### `safe`

Maximum safety, minimal auto-execution.

- **Default**: `deny` - Require explicit permission
- **Allow**: Read-only operations
- **Ask**: Most operations
- **Deny**: Destructive actions, network operations

#### `yolo`

High autonomy for rapid development.

- **Default**: `allow` - Auto-execute most actions
- **Allow**: Most file operations, shell commands, git
- **Ask**: System-level changes
- **Deny**: Very dangerous operations

#### `readonly`

No modifications allowed. Pure analysis mode.

- **Default**: `deny`
- **Allow**: Read operations only
- **Ask**: Nothing
- **Deny**: All write operations

#### `auto`

Fully autonomous. Use with extreme caution.

- **Default**: `allow`
- **Allow**: All operations
- **Ask**: Nothing
- **Deny**: Nothing (except explicit denies)

### Custom Profiles

Create custom profiles in `~/.ai-coder/profiles.json`:

```json
{
  "custom": {
    "default": "ask",
    "allow": ["read_file", "list_files", "search"],
    "ask": ["write_file", "shell", "git_commit"],
    "deny": ["rm -rf", "dd", "mkfs"]
  }
}
```

### Switching Profiles

```
/profile set yolo
/profile set default
```

Or via environment:

```bash
export AICODER_PROFILE=yolo
```

## Model Routing

KRYTH supports multiple models with automatic routing based on task complexity.

### Model Configuration

Set in config or environment:

```bash
export MAIN_MODEL=gpt-4-turbo
export PLANNER_MODEL=gpt-4-turbo
export SUMMARIZER_MODEL=gpt-3.5-turbo
```

### Auto-Routing

KRYTH can automatically select models based on:

- **Task type**: Planning uses PLANNER_MODEL, summarization uses SUMMARIZER_MODEL
- **Token count**: Large contexts may use models with bigger windows
- **Cost optimization**: Simple tasks can use cheaper models

Configure routing in `~/.ai-coder/routing.json`:

```json
{
  "auto_route": true,
  "rules": [
    {"pattern": "plan", "model": "gpt-4-turbo"},
    {"pattern": "summarize", "model": "gpt-3.5-turbo"},
    {"max_tokens": 4000, "model": "gpt-3.5-turbo"}
  ]
}
```

### View Current Routing

```
/models
```

## Memory System

KRYTH uses a layered memory system to maintain context.

### Memory Layers

1. **Session Memory** - Current conversation (volatile)
2. **Project Memory** - Project-specific instructions (persisted)
3. **User Memory** - Global user preferences (persisted)
4. **System Memory** - Built-in instructions (read-only)

### Memory Files

#### Project Memory

Location: Project root

Files (in order of priority):
- `AGENTS.md` - Primary project instructions
- `KRYTH.md` - Alternative project memory
- `.krythrc` - Legacy format (still supported)

Example `AGENTS.md`:

```markdown
# Project Guidelines

## Style
- Use type hints
- Follow PEP 8
- Docstrings for all public APIs

## Architecture
- Layered architecture
- Dependency injection
- Test-driven development

## Tools
- Use pytest for testing
- Use black for formatting
- Use mypy for type checking
```

#### User Memory

Location: `~/.ai-coder/memory.md`

Example:

```markdown
# Global Preferences

## Coding Style
- Prefer functional programming
- Use dataclasses over namedtuples
- Always include error handling

## My Projects
- Primary language: Python
- Preferred framework: FastAPI
- Database: PostgreSQL
```

### Managing Memory

```
/memory              # List layers
/memory show         # Display contents
/memory edit         # Edit project memory
/memory path         # Show file paths
```

### Memory Loading Order

1. System memory (built-in)
2. User memory (global)
3. Project memory (local)
4. Session memory (current)

Later layers can override earlier ones.

## Logging

### Log Levels

- `DEBUG` - Detailed diagnostic information
- `INFO` - General informational messages
- `WARNING` - Warning messages
- `ERROR` - Error messages
- `CRITICAL` - Critical errors

### Setting Log Level

```bash
export LOG_LEVEL=DEBUG
```

Or in config:

```json
{
  "log_level": "DEBUG"
}
```

### Log File

Default: `~/.kryth/kryth.log`

View logs:

```
/log
/log 100
/log path
/log clear
```

### Structured Logging

Logs include:
- Timestamp
- Log level
- Component (agent, tools, ui, etc.)
- Message
- Context (session ID, tool name, etc.)

Example:

```
2024-01-15 10:30:45 [INFO] [agent] Session started: abc123
```

## Advanced Settings

### Request Timeout

```bash
export REQUEST_TIMEOUT=120
```

### Max Tokens

```bash
export MAX_TOKENS=8192
```

### Auto-Resume

Control automatic session resume:

```bash
export AICODER_NO_RESUME=true  # Disable
```

### Assume Yes

Auto-approve all actions (dangerous):

```bash
export AICODER_ASSUME_YES=true
```

### Custom Tool Paths

Add directories with custom tools:

```bash
export KRYTH_TOOL_PATH=~/my-tools:~/.kryth/tools
```

### Git Auto-Commit

Automatically commit changes:

```bash
export GIT_AUTO_COMMIT=true
```

### Session Store Location

Change where sessions are stored:

```bash
export KRYTH_SESSION_DIR=~/my-sessions
```

## Configuration Hierarchy

Settings are loaded in this order (later overrides earlier):

1. Defaults (hardcoded)
2. Config file (`~/.ai-coder/config.json`)
3. Environment variables
4. Command-line flags (when implemented)

## Validation

Run diagnostics to verify configuration:

```
/diag
```

This checks:
- API key presence
- Model accessibility
- Endpoint connectivity
- Permission profile validity

## Resetting Configuration

To reset to defaults:

```bash
# Backup current config
cp ~/.ai-coder/config.json ~/.ai-coder/config.json.backup

# Remove config
rm ~/.ai-coder/config.json

# Restart KRYTH
kryth
```

## Troubleshooting

### Config Not Loading

Check file location and permissions:

```bash
ls -la ~/.ai-coder/config.json
cat ~/.ai-coder/config.json
```

### Environment Variables Not Working

Ensure they're exported in the same shell:

```bash
export OPENAI_API_KEY=sk-...
kryth
```

Or use `.env` file in project root.

### Profile Not Switching

Verify profile exists:

```
/profile list
```

Check for typos:

```
/profile set yolo  # not "yolo"
```

## Next Steps

- See [Usage Guide](usage.md) for practical examples
- Check [Tools Reference](tools.md) for available tools
- Read [FAQ](faq.md) for common questions