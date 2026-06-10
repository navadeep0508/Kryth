# Contributing to KRYTH

Thank you for your interest in contributing to KRYTH! This document provides guidelines and information for contributors.

## Quick Links

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Code Style](#code-style)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Release Process](#release-process)

## Code of Conduct

This project adheres to a code of conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git
- (Optional) Docker for containerized development
- (Optional) Node.js for frontend work

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR-USERNAME/KRYTH-Autonomous-AI-Coding-Agent.git
   cd KRYTH-Autonomous-AI-Coding-Agent
   ```
3. Add the upstream remote:
   ```bash
   git remote add upstream https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent.git
   ```

## Development Setup

### 1. Create Virtual Environment

```bash
# Using uv (recommended)
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Or using venv
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 2. Install Dependencies

```bash
# Install in editable mode with all extras
uv pip install -e ".[bridge]"

# Or using pip
pip install -e ".[bridge]"
```

### 3. Install Pre-commit Hooks

```bash
pre-commit install
```

This will run formatting and linting checks automatically on commit.

### 4. Set Up Environment Variables

```bash
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY or other provider keys
```

### 5. Install Playwright (for bridge features)

```bash
playwright install chromium
```

## Project Structure

```
kryth/
├── src/
│   ├── kryth/           # Main package (CLI entry point)
│   │   ├── main.py      # Entry point
│   │   ├── config.py    # Configuration management
│   │   └── _repl_main.py # REPL loop
│   └── agent/           # Core agent package
│       ├── agent_loop.py
│       ├── skills.py
│       ├── tools/       # Tool implementations
│       ├── ui/          # User interface components
│       ├── bridge/      # Browser-based provider bridge
│       └── ...
├── tests/               # Test suite
├── docs/                # Documentation
├── .env.example         # Environment template
├── pyproject.toml       # Project configuration
├── Makefile            # Development shortcuts
└── README.md           # Project overview
```

## Making Changes

### Branch Strategy

1. Create a new branch for your feature/fix:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

2. Make your changes following the code style guidelines.

3. Commit with clear, descriptive messages:
   ```bash
   git commit -m "feat: add new tool for code analysis"
   git commit -m "fix: handle null pointer in session persistence"
   ```

### Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Formatting, missing semicolons, etc. (no code change)
- `refactor:` Code restructuring
- `test:` Adding or updating tests
- `chore:` Maintenance tasks

## Code Style

### Python

- Follow [PEP 8](https://pep8.org/)
- Use [Black](https://black.readthedocs.io/) for formatting (auto-formatted on commit)
- Use [isort](https://pycqa.github.io/isort/) for import sorting
- Use [flake8](https://flake8.pycqa.org/) for linting
- Use [mypy](http://mypy-lang.org/) for type checking

### Type Hints

All new code should include type hints. Use:

```python
def function_name(param: str, optional: Optional[int] = None) -> List[str]:
    ...
```

### Docstrings

Use Google-style docstrings:

```python
def function_name(param: str) -> str:
    """Brief description.

    Longer description if needed.

    Args:
        param: Description of param.

    Returns:
        Description of return value.
    """
```

## Testing

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=agent --cov-report=html

# Run specific test file
pytest tests/test_specific.py

# Run with verbose output
pytest -v
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files `test_*.py`
- Use descriptive test names
- Aim for high coverage on new code
- Use fixtures from `tests/conftest.py`

### Test Guidelines

- Unit tests for individual functions/classes
- Integration tests for tool interactions
- Mock external API calls
- Test error cases and edge conditions

## Submitting Changes

### 1. Update Your Branch

```bash
git fetch upstream
git rebase upstream/main
```

### 2. Run Quality Checks

```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Lint
flake8 src/ tests/

# Type check
mypy src/

# Run tests
pytest --cov=agent
```

### 3. Push to Your Fork

```bash
git push origin feature/your-feature-name
```

### 4. Create Pull Request

1. Go to the repository on GitHub
2. Click "New Pull Request"
3. Select your branch
4. Fill out the PR template:
   - Clear title and description
   - Link to related issues
   - Screenshots for UI changes
   - Testing instructions
5. Submit the PR

### PR Review Process

- All PRs require at least one review from a maintainer
- Address review comments promptly
- Keep PRs focused (one feature/fix per PR)
- Squash commits before merging (or use merge commits as appropriate)

## Release Process

Maintainers handle releases. The process:

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Create git tag: `git tag -a v1.0.9 -m "Release 1.0.9"`
4. Push tag: `git push origin v1.0.9`
5. GitHub Actions builds and publishes to PyPI

## Development Tools

### Makefile Commands

```bash
make install      # Install dependencies
make dev          # Start development environment
make test         # Run tests
make lint         # Run linters
make format       # Format code
make type-check   # Run type checking
make clean        # Clean build artifacts
```

### Using the CLI

```bash
# Install locally
pip install -e .

# Run KRYTH
kryth

# Run with debug logging
LOG_LEVEL=DEBUG kryth
```

## Debugging

### Enable Debug Logging

```bash
export LOG_LEVEL=DEBUG
kryth
```

Logs are written to `~/.kryth/kryth.log` by default.

### Use /diag Command

Inside KRYTH REPL:
```
/diag
```

Shows model configuration and health checks.

## Common Issues

### Import Errors

If you see `ModuleNotFoundError: No module named 'agent'`:

```bash
# Ensure you're in the project root
cd /path/to/KRYTH-Autonomous-AI-Coding-Agent
pip install -e .
```

### Bridge Module Missing

Install bridge dependencies:
```bash
pip install -e ".[bridge]"
playwright install chromium
```

### API Key Not Found

Create `.env` file with your `OPENAI_API_KEY` or other provider keys.

## Getting Help

- Check existing [Issues](https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent/issues)
- Join our [Discord](https://discord.gg/kryth)
- Email: dev@kryth.ai

## Recognition

Contributors will be listed in:
- README.md contributors section
- CHANGELOG.md
- Release notes

Thank you for contributing to KRYTH! 🚀