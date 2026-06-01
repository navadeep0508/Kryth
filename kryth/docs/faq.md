# Frequently Asked Questions

Find answers to common questions about KRYTH.

## General

### What is KRYTH?

KRYTH is an autonomous AI coding agent that builds, debugs, and deploys entire applications with minimal human intervention. It's a terminal-based tool that acts as your AI pair programmer, capable of generating complete projects from a single prompt.

### How is KRYTH different from GitHub Copilot?

GitHub Copilot provides inline code suggestions as you type. KRYTH is **autonomous** - it can build entire applications, manage projects, run tests, and deploy to production with minimal human input. KRYTH is more like having a senior developer working for you, not just an autocomplete tool.

### Is KRYTH free?

Yes! KRYTH is open-source (MIT license) and free to use. You only pay for the AI model API calls (OpenAI, Anthropic, etc.). Some AI providers offer free tiers that work with KRYTH.

### Which AI models does KRYTH support?

KRYTH supports:
- OpenAI (GPT-4, GPT-3.5-turbo)
- Anthropic (Claude 3)
- Google (Gemini)
- Groq
- Together AI
- Local models via Ollama
- Any OpenAI-compatible API

### Is my code secure?

KRYTH runs locally on your machine. Your code never leaves your environment unless you explicitly share it. API calls are made directly to the AI provider (OpenAI, etc.) over HTTPS. We don't store or log your code.

### What platforms does KRYTH support?

KRYTH works on:
- Linux (Ubuntu, Debian, Fedora, Arch, etc.)
- macOS (10.15+)
- Windows (10+, with WSL recommended)
- Any platform with Python 3.10+

### Can KRYTH work with my existing project?

Yes! KRYTH can analyze and work with existing codebases. Point it at your project directory and it will understand the structure, dependencies, and patterns.

## Installation

### "Command not found: kryth"

Add Python Scripts to your PATH:

**Windows:**
```
C:\Users\YourName\AppData\Roaming\Python\Python3x\Scripts
```

**macOS/Linux:**
```
~/.local/bin
```

Or reinstall with:
```bash
pip install --user kryth
```

### "ModuleNotFoundError: No module named 'agent'"

This means the agent package isn't on your Python path. Fix:

```bash
# Reinstall in editable mode
pip install -e .
```

### How do I uninstall KRYTH?

```bash
pip uninstall kryth
```

To remove config and session data:
- Linux/macOS: `rm -rf ~/.kryth ~/.ai-coder`
- Windows: `rmdir /s %APPDATA%\kryth` and `rmdir /s %APPDATA%\ai-coder`

## Configuration

### Where is my config stored?

- Linux/macOS: `~/.ai-coder/config.json`
- Windows: `%APPDATA%\ai-coder\config.json`

### How do I change the AI model?

Edit config:
```
/config
```

Or set environment variable:
```bash
export MAIN_MODEL=gpt-4-turbo
```

### What's the difference between profiles?

- **default**: Balanced - asks before sensitive operations
- **safe**: High security - requires confirmation for most actions
- **yolo**: High autonomy - auto-executes most operations
- **readonly**: Analysis only - no file modifications
- **auto**: Fully autonomous (use with caution)

Switch with:
```
/profile set yolo
```

### How do I reset KRYTH to defaults?

```bash
rm ~/.ai-coder/config.json
kryth
```

## Usage

### How do I save my work?

KRYTH automatically saves sessions. Your work is persisted in:
- `~/.kryth/sessions/` - Conversation history
- Project files - Generated/modified code
- `AGENTS.md` - Project memory (if created)

### Can I use KRYTH offline?

No, KRYTH requires an internet connection to access AI models. However, you can use local models via Ollama if you have one running locally.

### How do I stop KRYTH?

- Type `exit` and press Enter
- Press `Ctrl+D`
- Press `Ctrl+C` twice

### How do I interrupt a running operation?

Press `Ctrl+C` to cancel the current operation and return to the prompt.

### Can I use KRYTH in scripts?

Yes! You can pipe prompts to KRYTH:

```bash
echo "Create a Python script that prints 'Hello World'" | kryth
```

Or use it in a script:

```bash
#!/bin/bash
kryth "Refactor this code: $(cat input.py)" > output.py
```

## Features

### What tools does KRYTH have?

KRYTH includes tools for:
- Code generation and editing
- Debugging and error analysis
- Testing (unit, integration, e2e)
- Git operations
- File operations (read, write, search)
- Shell command execution
- Web searching
- Code review and critique
- Documentation generation
- Deployment to cloud platforms
- Database operations
- And more...

See all tools:
```
/tools
```

### Can KRYTH write tests?

Yes! Use:
```
/test --coverage --output tests/
```

Or ask directly:
```
Write unit tests for the user module with 90% coverage
```

### Can KRYTH deploy my app?

Yes! KRYTH can deploy to:
- Vercel
- AWS
- Google Cloud
- Azure
- Heroku
- Railway
- DigitalOcean
- And more...

```
/deploy --platform vercel
```

### Does KRYTH support multiple languages?

Yes! KRYTH works with:
- Python
- JavaScript/TypeScript
- Go
- Rust
- Java
- C++
- Ruby
- PHP
- Swift
- Kotlin
- And many more...

### Can KRYTH work with my framework?

KRYTH understands popular frameworks:
- **Python**: FastAPI, Django, Flask, Pyramid, Tornado
- **JavaScript**: React, Next.js, Vue, Angular, Svelte, Node.js
- **Go**: Gin, Echo, Fiber
- **Rust**: Actix, Rocket, Axum
- **Java**: Spring, Jakarta EE
- **And many others...**

## Troubleshooting

### KRYTH is slow

First run is always slower (model caching). If consistently slow:
- Check your internet connection
- Try a different model (some are faster)
- Use `/diag` to check API latency
- Consider using a local model with Ollama

### API rate limits

If you hit rate limits:
- Switch to a different model
- Add delays between requests
- Upgrade your API plan
- Use multiple API keys with model routing

### "Context length exceeded"

Your conversation is too long. Solutions:
- Use `/clear` to start fresh
- Use `/memory` to offload context to files
- Switch to a model with larger context window
- Enable summarization (automatic in planning mode)

### Bridge mode not working

Ensure you installed bridge dependencies:
```bash
pip install -e ".[bridge]"
playwright install chromium
```

Then start bridge:
```
/bridge start gemini
```

### Permission denied errors

Check your profile. If using `safe` or `readonly`, KRYTH may block file operations. Switch to `default` or `yolo`:
```
/profile set default
```

### Logs are empty

Log file location:
```
/log path
```

Ensure the directory is writable:
```bash
touch ~/.kryth/kryth.log
```

## Performance

### How fast is KRYTH?

Response times vary by model:
- GPT-4: 5-30 seconds
- GPT-3.5-turbo: 2-10 seconds
- Claude 3: 3-20 seconds
- Local models (Ollama): 1-10 seconds (depending on hardware)

### Token usage

Check usage:
```
/tokens
```

Typical costs:
- GPT-4: ~$0.03/1K input, $0.06/1K output
- GPT-3.5-turbo: ~$0.001/1K input, $0.002/1K output
- Claude 3 Sonnet: ~$0.003/1K input, $0.015/1K output

### Can I use KRYTH with limited API budget?

Yes! Strategies:
- Use GPT-3.5-turbo for most tasks
- Reserve GPT-4 for complex planning
- Set token limits in config
- Monitor usage with `/tokens`
- Use local models (Ollama) for free

## Advanced

### Can I create custom tools?

Yes! KRYTH has a plugin system. Create a Python file in `~/.kryth/tools/`:

```python
from agent.tools import tool

@tool
def my_custom_tool(arg1: str, arg2: int) -> str:
    """Tool description."""
    # Your code
    return result
```

### How do I add my own commands?

Create a skill file in `~/.kryth/skills/`:

```python
from agent.skills import skill

@skill
def my_skill(text: str) -> None:
    """Skill description."""
    # Your code
```

Then use: `/my_skill argument`

### Can I use KRYTH in my CI/CD pipeline?

Yes! Use KRYTH in non-interactive mode:

```bash
kryth "Run tests and generate coverage report" --non-interactive
```

Or in GitHub Actions:

```yaml
- name: AI Code Review
  run: |
    pip install kryth
    kryth "Review the changes in this PR"
```

### How do I contribute?

See [CONTRIBUTING.md](CONTRIBUTING.md). We welcome contributions!

### Where can I get help?

- **Documentation**: https://kryth.vercel.app/
- **Discord**: https://discord.gg/kryth
- **GitHub Issues**: https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent/issues
- **Email**: support@kryth.ai

## Licensing

### What license is KRYTH?

MIT License - you can use KRYTH for any purpose, including commercial projects.

### Can I modify KRYTH?

Yes! KRYTH is open-source. Fork it, modify it, and use it however you like. Just keep the license file.

### Can I use KRYTH commercially?

Absolutely. The MIT license allows commercial use without restrictions.

## Roadmap

### What's coming next?

See [CHANGELOG.md](CHANGELOG.md) for planned features:
- Multi-modal support (images, diagrams)
- VS Code and JetBrains plugins
- Collaborative sessions
- Enterprise features (SSO, audit logs)
- Self-improvement capabilities

### How can I request features?

Open an issue on GitHub or join our Discord and share your ideas!

---

**Didn't find your answer?** Reach out on Discord or open an issue. We're here to help!