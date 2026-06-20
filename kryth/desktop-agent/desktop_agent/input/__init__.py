"""Input controllers for desktop automation."""

from .mouse import MouseController, mouse
from .keyboard import KeyboardController, keyboard
from .clipboard import ClipboardController, clipboard

__all__ = [
    "MouseController",
    "mouse",
    "KeyboardController",
    "keyboard",
    "ClipboardController",
    "clipboard",
]