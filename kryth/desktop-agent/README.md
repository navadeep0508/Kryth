# Desktop Agent

A Python-based desktop automation framework for controlling mouse, keyboard, windows, and screen capture.

## Features

### Phase 1 Capabilities
- **Mouse Control**: Move, click, scroll, drag with smooth movements
- **Keyboard Control**: Type text, press keys, hotkeys, copy/paste
- **Clipboard**: Copy, paste, clear with retry logic
- **Window Management**: List windows, find by title, focus, switch
- **Screen Capture**: Capture monitors, save screenshots

## Installation

```bash
pip install -r requirements.txt
```

## Project Structure

```
desktop-agent/
├── desktop_agent/          # Main package
│   ├── config.py          # Configuration settings
│   ├── input/             # Input controllers
│   │   ├── mouse.py      # Mouse controller
│   │   ├── keyboard.py   # Keyboard controller
│   │   └── clipboard.py  # Clipboard controller
│   ├── windows/          # Window management
│   │   └── window_manager.py
│   ├── vision/           # Screen capture
│   │   └── capture.py
│   ├── core/             # Core utilities
│   ├── automation/       # Automation workflows
│   ├── dashboard/        # UI dashboard
│   └── tools/            # Helper tools
├── examples/             # Example scripts
│   └── phase1_demo.py   # Phase 1 demonstration
├── tests/                # Unit tests
│   └── test_phase1.py   # Phase 1 tests
├── main.py              # Main demo script
└── requirements.txt     # Dependencies
```

## Usage

### Run the Phase 1 Demo

```bash
python main.py
```

This demonstrates:
- Opening an application (Calculator)
- Moving the mouse
- Typing text
- Capturing the screen
- Copy/paste operations
- Switching windows
- Closing the application

### Run Tests

```bash
pytest tests/ -v
```

## Configuration

Edit `desktop_agent/config.py` to customize:
- Mouse movement duration
- Keyboard typing interval
- Window focus timeout
- Screenshot settings
- Clipboard retry attempts
- Fail-safe behavior

## Requirements

- Python 3.8+
- pyautogui
- pygetwindow
- psutil
- mss
- pyperclip
- pillow
- numpy

## Notes

- The demo script requires a display (X11 on Linux, GUI on Windows/macOS)
- Tests use mocking and can run headless
- Enable fail-safe in config to abort automation by moving mouse to corner

## License

MIT