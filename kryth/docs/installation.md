# Installation Guide

This guide covers installing KRYTH on various platforms and environments.

## Prerequisites

- **Python**: 3.10 or higher
- **pip**: Latest version recommended
- **Git**: For version control operations

## Standard Installation

### Using pip (Recommended)

```bash
pip install kryth
```

This installs KRYTH with core dependencies.

### With Bridge Support

For browser-based authentication (Gemini, Claude via browser):

```bash
pip install kryth[bridge]
```

Or:

```bash
pip install "kryth[bridge]"
```

### Using uv (Faster)

```bash
uv pip install kryth
```

### Using Poetry

```bash
poetry add kryth
```

## Development Installation

For contributors or if you want to modify KRYTH:

```bash
# Clone the repository
git clone https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent.git
cd KRYTH-Autonomous-AI-Coding-Agent

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in editable mode
pip install -e ".[bridge]"

# Install pre-commit hooks
pre-commit install

# Install Playwright browsers
playwright install chromium
```

## Docker Installation

### Using Docker Hub (when available)

```bash
docker pull navadeep0508/kryth:latest
docker run -it --rm -v $(pwd):/workspace navadeep0508/kryth
```

### Using Docker Compose

```bash
# Build and run
docker-compose up -d
docker-compose exec kryth kryth

# Stop
docker-compose down
```

### Manual Docker Build

```bash
docker build -t kryth:latest .
docker run -it --rm -v $(pwd):/workspace kryth:latest
```

## Platform-Specific Notes

### Windows

1. Install Python 3.10+ from python.org
2. Ensure Python is added to PATH
3. Open PowerShell or Command Prompt
4. Run `pip install kryth`
5. If you get permission errors, try:
   ```powershell
   pip install --user kryth
   ```

### macOS

```bash
# Using Homebrew Python
brew install python
pip3 install kryth

# Or with Homebrew (if available)
brew install kryth
```

### Linux

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3 python3-pip
pip3 install --user kryth

# Fedora
sudo dnf install python3 python3-pip
pip3 install --user kryth

# Arch
sudo pacman -S python python-pip
pip install kryth
```

## Post-Installation

### 1. Set Up API Key

Create a `.env` file in your project root or home directory:

```bash
# Copy the example
cp .env.example .env

# Edit and add your API key
# OPENAI_API_KEY=sk-...
```

Or set it as an environment variable:

```bash
export OPENAI_API_KEY=your-api-key-here
```

### 2. Verify Installation

```bash
# Check version
kryth --version

# Run help
kryth --help

# Start KRYTH
kryth
```

### 3. Install Playwright Browsers (for bridge mode)

```bash
playwright install chromium
```

## Troubleshooting

### Command Not Found

If `kryth` command is not found:

```bash
# Check if Python Scripts directory is in PATH
# On Windows: C:\Users\YourName\AppData\Roaming\Python\Python3x\Scripts
# On macOS/Linux: ~/.local/bin

# Add to PATH temporarily
export PATH=$PATH:~/.local/bin

# Or install with --user flag
pip install --user kryth
```

### Import Errors

If you see `ModuleNotFoundError: No module named 'agent'`:

```bash
# Reinstall in editable mode
pip install -e .
```

### Permission Errors

On Linux/macOS, you might need:

```bash
# Install without sudo
pip install --user kryth

# Or use a virtual environment
python -m venv .venv
source .venv/bin/activate
pip install kryth
```

### Bridge Module Missing

```bash
pip install -e ".[bridge]"
playwright install chromium
```

### API Key Not Recognized

1. Ensure `.env` file is in the current working directory
2. Or set `OPENAI_API_KEY` environment variable
3. Check that the key is valid and has credits
4. Run `/diag` inside KRYTH to verify configuration

### Slow First Run

The first run may be slow as KRYTH:
- Downloads and caches models
- Initializes the session store
- Builds the tool registry

Subsequent runs will be much faster.

## Uninstallation

```bash
pip uninstall kryth
```

To also remove configuration and session data:

```bash
# Linux/macOS
rm -rf ~/.kryth
rm -rf ~/.ai-coder

# Windows
rmdir /s %APPDATA%\kryth
rmdir /s %APPDATA%\ai-coder
```

## Next Steps

- Read the [Usage Guide](usage.md)
- Learn about [Configuration](configuration.md)
- Explore [Tools Reference](tools.md)
- Check the [FAQ](faq.md)

## Getting Help

If you encounter issues not covered here:
- Search [existing issues](https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent/issues)
- Join our [Discord](https://discord.gg/kryth)
- Email: support@kryth.ai