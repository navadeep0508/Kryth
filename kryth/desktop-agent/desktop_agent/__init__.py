"""Desktop Agent - A Python-based desktop automation framework."""

__version__ = "0.1.0"
__author__ = "Kryth Team"

# Import main components for easy access
from .config import config
from .input import mouse, keyboard, clipboard
from .windows import window_manager
from .vision import capture

__all__ = [
    "config",
    "mouse",
    "keyboard",
    "clipboard",
    "window_manager",
    "capture",
]