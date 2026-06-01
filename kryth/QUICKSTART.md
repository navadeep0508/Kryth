# KRYTH Quickstart Guide

Get up and running with KRYTH in 5 minutes.

## Prerequisites

- Python 3.10 or higher
- pip (Python package installer)
- An OpenAI API key or compatible service key

## Installation

```bash
pip install kryth
```

That's it! KRYTH is now installed.

## First Run

### 1. Set Your API Key

Create a `.env` file in your project directory:

```bash
# Copy the example
cp .env.example .env

# Edit .env and add your API key
# OPENAI_API_KEY=sk-your-key-here
```

Or set it as an environment variable:

```bash
export OPENAI_API_KEY=sk-your-key-here
```

### 2. Start KRYTH

```bash
kryth
```

You should see the KRYTH banner and a prompt:

```
KRYTH 1.0.8 · gpt-4-turbo-preview
────────────────────────────────────────────────────────────────────────────
Ready. Type a request, or /help for commands.
>
```

### 3. Try Your First Command

```
> Create a simple Python hello world script
```

KRYTH will generate a `hello.py` file with a complete, working program.

## Common First Tasks

### Task 1: Generate a Web API

```
> Create a FastAPI application with:
- User authentication using JWT
- PostgreSQL database with SQLAlchemy
- CRUD operations for a Task model
- Docker deployment configuration
```

### Task 2: Debug Existing Code

```
> Analyze this code for bugs and security issues:
[Paste your code or point to a file]
```

### Task 3: Write Tests

```
> Generate unit tests for the user service module with 90% coverage
```

### Task 4: Refactor for Performance

```
> Optimize this database query to avoid N+1 problems:
[Your query code]
```

## Essential Commands

Learn these key commands to master KRYTH:

| Command | Purpose |
|---------|---------|
| `/help` | Show all commands |
| `/status` | See current session info |
| `/diag` | Check configuration |
| `/config` | Open config editor |
| `/profile` | Change permission level |
| `/memory` | View project instructions |
| `/session` | Resume previous work |
| `/clear` | Start fresh |
| `/plan` | Enable planning mode |

## Understanding Profiles

KRYTH has different autonomy levels:

- **default** - Balanced, asks before important actions (recommended)
- **safe** - Maximum verification, minimal auto-execution
- **yolo** - High autonomy, fewer confirmations (for experiments)
- **readonly** - Analysis only, no file modifications

Switch profiles:

```
/profile set yolo
```

## Using Planning Mode

For complex tasks, enable planning:

```
/plan
```

Then describe your task. KRYTH will create a step-by-step plan before executing.

View progress:

```
/todos
```

## Session Management

KRYTH automatically saves your sessions. Resume later:

```
/session latest
```

Or list all sessions:

```
/session
```

## Configuration Tips

### Edit Config Visually

```
/config
```

Navigate with arrow keys, Enter to edit, Esc to save.

### Add Project Instructions

Create `AGENTS.md` in your project root:

```markdown
# Project Guidelines

- Use type hints
- Follow PEP 8
- Write docstrings
- Test coverage >80%
```

KRYTH will follow these guidelines automatically.

## Next Steps

- Read the full [Usage Guide](docs/usage.md)
- Learn about [Configuration](docs/configuration.md)
- Explore all [Tools](docs/tools.md)
- Check the [FAQ](docs/faq.md)

## Getting Help

- Run `/help` inside KRYTH
- Visit https://kryth.vercel.app/
- Join our Discord: https://discord.gg/kryth
- Open an issue: https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent/issues

## Troubleshooting

### "API key not found"

Ensure `.env` file exists in current directory or `OPENAI_API_KEY` is set:

```bash
echo "OPENAI_API_KEY=sk-..." > .env
```

### "Module not found: agent"

Reinstall:

```bash
pip install -e .
```

### Bridge features not working

Install bridge dependencies:

```bash
pip install -e ".[bridge]"
playwright install chromium
```

### Slow first run

Normal! KRYTH caches models and initializes on first run. Subsequent runs are faster.

## What's Next?

Now that you're up and running:

1. Explore different profiles (`/profile list`)
2. Try planning mode (`/plan`)
3. Add project memory (`/memory edit`)
4. Check diagnostics (`/diag`)
5. Read the full documentation

**Happy coding with KRYTH!** 🚀