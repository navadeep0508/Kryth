# Usage Guide

Complete guide to using KRYTH Autonomous AI Coding Agent.

## Table of Contents

- [Starting KRYTH](#starting-kryth)
- [REPL Commands](#repl-commands)
- [Slash Skills](#slash-skills)
- [Session Management](#session-management)
- [Configuration](#configuration-1)
- [Best Practices](#best-practices)
- [Examples](#examples)

## Starting KRYTH

### Basic Start

```bash
kryth
```

This launches the interactive REPL (Read-Eval-Print Loop).

### With Debug Logging

```bash
LOG_LEVEL=DEBUG kryth
```

### In a Specific Project

```bash
cd /path/to/your/project
kryth
```

KRYTH will automatically detect the project context.

## REPL Commands

KRYTH provides a rich set of slash commands for controlling the agent.

### Core Commands

| Command | Description |
|---------|-------------|
| `/help` | Show complete command list |
| `/status` | Display current session status |
| `/models` | Show model configuration |
| `/tools` | List available tools |
| `/config` | Open configuration TUI |
| `/profile` | View or change permission profile |
| `/memory` | Inspect project/user memory |
| `/session` | List or resume sessions |
| `/diag` | Run diagnostics and health checks |
| `/log` | View debug log |
| `/plan` | Toggle planning mode |
| `/todos` | Show current task list |
| `/tokens` | Display token usage |
| `/clear` | Reset session |
| `/bridge` | Manage browser provider bridge |

### Command Details

#### `/help

Shows an organized list of all available commands with descriptions.

```
/help
```

#### `/status

Displays current session information:
- Active model
- Current mode
- Permission profile
- Message count
- Token usage
- Tool call count
- Depth (conversation depth)

```
/status
```

#### `/models

Shows model routing configuration:
- Main model
- Planner model
- Summarizer model
- API endpoint
- Auto-route settings

```
/models
```

#### `/tools

Lists all available tools that KRYTH can use. Shows count of loaded tools.

```
/tools
```

#### `/config

Opens the interactive configuration editor. Navigate with arrow keys, Enter to edit.

```
/config
```

You can focus a specific key:

```
/config OPENAI_API_KEY
```

#### `/profile

Manage permission profiles:

```
/profile              # List all profiles
/profile show default # Show default profile rules
/profile set yolo     # Switch to yolo profile
```

Available profiles:
- `default` - Balanced, asks for confirmation on sensitive operations
- `safe` - High verification, minimal auto-execution
- `yolo` - High autonomy, fewer confirmations
- `readonly` - No file modifications
- `auto` - Fully autonomous (use with caution)

#### `/memory

Inspect memory layers:

```
/memory               # List loaded memory files
/memory show          # Display memory contents
/memory edit          # Open project memory in editor
/memory path          # Show memory file paths
```

Memory files:
- `AGENTS.md` - Project-scope instructions
- `KRYTH.md` - Alternative project memory
- `~/.ai-coder/memory.md` - User-global memory

#### `/session

Session management:

```
/session              # List recent sessions
/session latest       # Resume most recent session
/session <id>         # Resume specific session by ID prefix
```

#### `/diag

Run health checks on configured models and show configuration:

```
/diag
```

Output includes:
- Base URL
- Model names
- API key status (masked)
- Health check results for each model

#### `/log

View debug log:

```
/log                  # Last 50 lines
/log 100              # Last 100 lines (max 1000)
/log path             # Show log file location
/log clear            # Clear the log
```

#### `/plan

Toggle planning mode. When active, KRYTH will create detailed plans before execution.

```
/plan
```

#### `/todos

Show current task list from planning mode.

```
/todos
```

#### `/tokens

Display token usage statistics.

```
/tokens
```

#### `/clear

Reset the current session, clearing all messages.

```
/clear
```

#### `/bridge

Manage browser-based provider bridge:

```
/bridge start [gemini|claude|openai] [--port N]  # Start bridge
/bridge stop                                     # Stop bridge
/bridge status                                   # Check status
/bridge auth <provider>                          # Re-authenticate
/bridge sessions                                # List saved sessions
```

## Slash Skills

KRYTH supports "slash skills" - special commands that invoke specific capabilities.

### Using Skills

```
/skill_name [arguments]

# Examples:
/debug myfile.py
/test --coverage
/refactor --target performance
```

### Available Skills

Skills are dynamically loaded from the `skills/` directory. Common ones include:

- `/debug` - Debug code with automatic error detection
- `/test` - Generate and run tests
- `/refactor` - Refactor code for better quality
- `/analyze` - Analyze codebase structure
- `/docs` - Generate documentation
- `/deploy` - Deploy to cloud platform
- `/git` - Git operations (commit, push, etc.)
- `/search` - Search codebase
- `/review` - Code review and critique

List all available skills:

```
/skills
```

## Session Management

### Automatic Resume

KRYTH automatically offers to resume recent sessions if:
- Session is less than 24 hours old
- Session has at least 2 messages
- Running in interactive mode

### Manual Resume

```
/session latest
/session abc123  # Use session ID prefix
```

### Session Storage

Sessions are stored in:
- Linux/macOS: `~/.kryth/sessions/`
- Windows: `%APPDATA%\kryth\sessions\`

## Configuration

### Environment Variables

Set these in `.env` file or shell environment:

```bash
OPENAI_API_KEY=sk-...           # Required
OPENAI_BASE_URL=https://...     # Optional, default: OpenAI
MAIN_MODEL=gpt-4-turbo          # Optional
PLANNER_MODEL=gpt-4-turbo      # Optional
SUMMARIZER_MODEL=gpt-4-turbo   # Optional
LOG_LEVEL=INFO                  # Optional: DEBUG, INFO, WARNING, ERROR
AICODER_PROFILE=default        # Optional: default, safe, yolo, readonly
```

### Config File

Run `/config` to edit the config file stored at:
- Linux/macOS: `~/.ai-coder/config.json`
- Windows: `%APPDATA%\ai-coder\config.json`

### Memory Files

KRYTH uses memory files for context:

**Project Memory** (in project root):
- `AGENTS.md` - Primary project instructions
- `KRYTH.md` - Alternative project memory

**User Memory** (in home directory):
- `~/.ai-coder/memory.md` - Global instructions

Use `/memory edit` to open the project memory file.

## Best Practices

### 1. Start with Clear Goals

Be specific in your initial prompt:

```
Create a FastAPI application with:
- User authentication using JWT
- PostgreSQL database with SQLAlchemy
- CRUD operations for a Task model
- Docker deployment configuration
```

### 2. Use Planning Mode

For complex tasks, enable planning:

```
/plan
```

KRYTH will create a step-by-step plan before execution.

### 3. Leverage Memory

Add project-specific instructions to `AGENTS.md`:

```markdown
# Project Guidelines

- Use type hints everywhere
- Follow PEP 8 style guide
- Write docstrings for all public functions
- Tests should cover >80%
```

### 4. Choose the Right Profile

- `safe` - For production code, critical systems
- `default` - Balanced approach
- `yolo` - Rapid prototyping, experiments

### 5. Review Before Execution

For important changes:
- Use `default` or `safe` profile
- Watch tool calls as they happen
- Use `/todos` to track progress

### 6. Use Sessions

Work on multiple tasks in separate sessions:

```
/session new           # Start fresh session
/session my-feature    # Resume specific session
```

### 7. Monitor Token Usage

Check regularly:

```
/tokens
```

Consider switching to cheaper models for planning/summarizing.

### 8. Debug Effectively

When something goes wrong:

1. Check `/diag` for configuration issues
2. Review `/log` for error details
3. Use `/memory show` to verify context
4. Try `/clear` to reset if state is corrupted

### 9. Version Control

KRYTH can help with Git:

```
/git commit -m "message"  # Commit changes
/git push                 # Push to remote
/git status               # Check status
```

### 10. Use Bridge Mode for Free Providers

If you don't have API keys:

```
/bridge start gemini
```

Uses browser sessions instead.

## Examples

### Example 1: Create a Web App

```
kryth> Create a React + FastAPI application with:
- User registration and login
- PostgreSQL database
- JWT authentication
- Docker deployment
```

### Example 2: Debug an Issue

```
kryth> The application crashes when I POST to /api/users with invalid data. Here's the error:
[error trace]

kryth> Fix the validation and add proper error handling.
```

### Example 3: Refactor for Performance

```
kryth> Analyze this codebase for performance issues and refactor:
- Optimize database queries
- Add caching where appropriate
- Reduce N+1 query problems
```

### Example 4: Generate Tests

```
kryth> Write comprehensive unit tests for the user service module with >90% coverage.
```

### Example 5: Deploy to Production

```
kryth> Prepare this application for production deployment on AWS:
- Configure environment variables
- Set up logging
- Add health checks
- Create deployment scripts
```

## Keyboard Shortcuts

Inside the REPL:

- `Ctrl+C` - Cancel current operation / clear line
- `Ctrl+D` or `exit` - Exit KRYTH
- `Tab` - Autocomplete commands and skills
- `↑/↓` - Navigate command history
- `Enter` - Submit (or `Shift+Enter` for newline in multi-line input)

## Getting Help

- Run `/help` for command list
- Check `/diag` for configuration issues
- Review logs with `/log`
- See [FAQ](faq.md) for common questions
- Join [Discord](https://discord.gg/kryth) for community support

## Next Steps

- Learn about [Configuration](configuration.md)
- Explore [Tools Reference](tools.md)
- Read the [FAQ](faq.md)
- Contribute via [CONTRIBUTING.md](../CONTRIBUTING.md)