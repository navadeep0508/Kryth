# Running the Desktop Agent

This guide explains how to set up and run the Desktop Agent project.

## Prerequisites

- **Python 3.8+** must be installed on your system
- A display/GUI environment (X11 on Linux, Windows GUI, or macOS)
- Git (optional, for cloning the repository)

## Quick Start

### 1. Clone the Repository (if you haven't already)

```bash
git clone <repository-url>
cd desktop-agent
```

### 2. Install Dependencies

#### Option A: Using the Setup Script (Recommended)

**Linux/macOS:**
```bash
chmod +x setup.sh
./setup.sh
```

**Windows:**
```cmd
setup.bat
```

#### Option B: Manual Setup

Create and activate a virtual environment, then install dependencies:

**Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

**Windows:**
```cmd
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

### 3. Run the Phase 1 Demo

The demo script demonstrates all Phase 1 capabilities:

```bash
python main.py
```

**What the demo does:**
1. Opens Notepad (or Calculator on some systems)
2. Finds and activates the window
3. Moves the mouse to the text area
4. Types sample text
5. Copies text to clipboard
6. Captures a screenshot
7. Opens a second application (Calculator)
8. Switches back to the first window
9. Pastes clipboard content to verify copy worked
10. Takes a final screenshot
11. Closes all applications

**Output files:**
- `phase1_screenshot.png` - Screenshot after typing
- `phase1_final.png` - Final screenshot after paste

### 4. Run the Examples

The `examples/phase1_demo.py` script demonstrates each module in isolation:

```bash
python examples/phase1_demo.py
```

This runs through:
- Mouse operations (move, click, scroll)
- Keyboard operations (typing, hotkeys)
- Clipboard operations (copy, paste, availability check)
- Window operations (list windows, get active window)
- Screen capture (monitor info, capture, save)

### 5. Run Tests

The project includes comprehensive unit tests that use mocking and can run headless:

```bash
pytest tests/ -v
```

For a coverage report:

```bash
pytest tests/ -v --cov=desktop_agent --cov-report=html
```

Coverage report will be generated in `htmlcov/index.html`.

## Project Structure

```
desktop-agent/
├── desktop_agent/          # Main package
│   ├── __init__.py         # Package exports
│   ├── config.py          # Configuration settings
│   ├── input/             # Input controllers
│   │   ├── __init__.py
│   │   ├── mouse.py      # MouseController
│   │   ├── keyboard.py   # KeyboardController
│   │   └── clipboard.py  # ClipboardController
│   ├── windows/          # Window management
│   │   ├── __init__.py
│   │   └── window_manager.py  # WindowManager, WindowInfo
│   ├── vision/           # Screen capture
│   │   ├── __init__.py
│   │   └── capture.py    # ScreenCapture
│   ├── core/             # Core utilities (reserved for Phase 2+)
│   ├── automation/       # Automation workflows (reserved for Phase 2+)
│   ├── dashboard/        # UI dashboard (reserved for Phase 2+)
│   └── tools/            # Helper tools (reserved for Phase 2+)
├── examples/             # Example scripts
│   ├── __init__.py
│   └── phase1_demo.py   # Individual module demonstrations
├── tests/                # Unit tests
│   ├── __init__.py
│   ├── conftest.py      # Pytest fixtures and mocks
│   └── test_phase1.py   # Phase 1 test suite
├── main.py              # Main demo script
├── pyproject.toml       # Project metadata and dependencies
├── requirements.txt     # Dependencies (for pip)
├── setup.sh            # Linux/macOS setup script
├── setup.bat           # Windows setup script
├── README.md           # Project overview
└── RUNNING.md          # This file
```

## Configuration

Edit `desktop_agent/config.py` to customize behavior:

```python
@dataclass
class DesktopAgentConfig:
    # Mouse settings
    mouse_move_duration: float = 0.1      # Seconds for smooth movement
    mouse_click_pause: float = 0.05       # Pause after click
    mouse_scroll_amount: int = 5          # Lines to scroll

    # Keyboard settings
    keyboard_type_interval: float = 0.01  # Delay between keystrokes
    keyboard_hotkey_pause: float = 0.1    # Pause after hotkey

    # Window settings
    window_focus_timeout: float = 5.0     # Seconds to wait for window focus
    window_switch_delay: float = 0.2      # Delay after switching windows

    # Screenshot settings
    screenshot_monitor: int = 1           # Which monitor to capture (1 = primary)
    screenshot_format: str = "png"        # Output format

    # Clipboard settings
    clipboard_retry_attempts: int = 3     # Retry attempts for clipboard ops
    clipboard_retry_delay: float = 0.1    # Delay between retries

    # Safety settings
    fail_safe: bool = True                # Enable pyautogui fail-safe
    fail_safe_corner_pixels: int = 10     # Corner size for fail-safe

    # Logging
    log_level: str = "INFO"               # Logging level (DEBUG, INFO, WARNING, ERROR)
    log_file: Optional[str] = None        # Log file path (None = console only)
```

## Using the Desktop Agent in Your Code

```python
from desktop_agent import (
    config,
    mouse,
    keyboard,
    clipboard,
    window_manager,
    capture
)

# Move mouse
mouse.move_to(100, 200, duration=0.5)

# Type text
keyboard.type_text("Hello, World!", interval=0.02)

# Copy/paste
clipboard.copy("Some text")
pasted = clipboard.paste()

# Find and activate a window
notepad = window_manager.find_window(title="notepad")
if notepad:
    notepad.activate()

# Take a screenshot
capture.save_screenshot("screenshot.png", monitor=1)
```

## Troubleshooting

### Import Errors on Linux

If you get `Xlib.error.XauthError` or similar display errors, you need to run the code in a graphical environment. The tests use mocking and will work headless, but the demo requires a display.

**Solution:** Run on a desktop system with X11/Wayland, or use a virtual display:

```bash
# Install Xvfb (virtual framebuffer)
sudo apt-get install xvfb

# Run tests in virtual display
xvfb-run pytest tests/ -v

# Run demo in virtual display (will not show actual GUI)
xvfb-run python main.py
```

### Permission Errors on Linux

Some operations may require additional permissions.

**Solution:** Ensure your user has access to the display:

```bash
xhost +local:
```

### pyautogui Fail-Safe

If the mouse moves to the corner and the program aborts, that's the fail-safe feature. Move the mouse to any corner to abort automation if something goes wrong.

To disable: set `config.fail_safe = False` (not recommended).

### Clipboard Not Working on Linux

The clipboard may require an X server running.

**Solution:** Install `xclip` or `xsel`:

```bash
sudo apt-get install xclip
# or
sudo apt-get install xsel
```

### Windows-Specific Issues

- Ensure you're running in a standard Python environment (not WSL without display)
- Some window operations may require running as administrator for certain applications

### macOS-Specific Issues

- You may need to grant accessibility permissions to your terminal/IDE
- For keyboard shortcuts, the code automatically uses `command` instead of `ctrl`

## Running Without Installation

You can run the demo directly without installing the package by ensuring the project root is in the Python path:

```bash
cd desktop-agent
PYTHONPATH=. python main.py
```

## Development

After making changes to the code, reinstall in editable mode to pick up changes:

```bash
pip install -e .
```

This is not strictly necessary for pure Python modules, but it's good practice.

## Next Steps

- Explore the code in `desktop_agent/` to understand the implementation
- Check the `examples/` directory for usage patterns
- Read the tests in `tests/test_phase1.py` to see how each component is tested
- Look at `pyproject.toml` for project configuration and dependencies

## License

MIT License - see LICENSE file for details.