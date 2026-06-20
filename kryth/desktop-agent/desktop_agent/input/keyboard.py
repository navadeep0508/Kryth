"""Keyboard control module for desktop automation.

Provides high-level keyboard operations with safety checks.
"""

import logging
import time
from typing import List, Optional

import pyautogui

from desktop_agent.config import config

logger = logging.getLogger(__name__)

# Configure pyautogui settings
pyautogui.PAUSE = config.keyboard_hotkey_pause
pyautogui.KEYBOARD_KEYS_TO_IGNORE = [
    "shift", "ctrl", "alt", "cmd", "win", "right", "left", "up", "down",
    "pageup", "pagedown", "home", "end", "insert", "delete"
]


class KeyboardController:
    """Controller for keyboard operations."""
    
    def __init__(self):
        """Initialize the keyboard controller."""
        logger.debug("KeyboardController initialized")
    
    def type_text(
        self,
        text: str,
        interval: Optional[float] = None,
        pause: float = 0.0
    ) -> None:
        """Type a string of text.
        
        Args:
            text: Text to type
            interval: Delay between keystrokes (seconds)
            pause: Pause after each character (deprecated, use interval)
        """
        if interval is None:
            interval = config.keyboard_type_interval
        
        logger.info(f"Typing text (length={len(text)}, interval={interval:.3f}s)")
        try:
            pyautogui.typewrite(text, interval=interval)
        except Exception as e:
            logger.error(f"Failed to type text: {e}")
            raise
    
    def press(self, key: str, presses: int = 1, interval: float = 0.0) -> None:
        """Press a single key.
        
        Args:
            key: Key to press (e.g., 'enter', 'esc', 'tab')
            presses: Number of times to press
            interval: Interval between presses
        """
        logger.info(f"Pressing key '{key}' {presses}x")
        try:
            pyautogui.press(key, presses=presses, interval=interval)
        except Exception as e:
            logger.error(f"Failed to press key '{key}': {e}")
            raise
    
    def hotkey(self, *keys: str, interval: float = 0.0) -> None:
        """Press a combination of keys (hotkey).
        
        Args:
            *keys: Keys to press simultaneously (e.g., 'ctrl', 'c')
            interval: Interval between key presses
        """
        keys_str = "+".join(keys)
        logger.info(f"Pressing hotkey: {keys_str}")
        try:
            pyautogui.hotkey(*keys, interval=interval)
        except Exception as e:
            logger.error(f"Failed to press hotkey {keys_str}: {e}")
            raise
    
    def hold(self, key: str, duration: float) -> None:
        """Hold a key for a specific duration.
        
        Args:
            key: Key to hold
            duration: Time in seconds to hold
        """
        logger.info(f"Holding key '{key}' for {duration:.2f}s")
        try:
            pyautogui.keyDown(key)
            time.sleep(duration)
            pyautogui.keyUp(key)
        except Exception as e:
            logger.error(f"Failed to hold key '{key}': {e}")
            # Ensure key is released
            try:
                pyautogui.keyUp(key)
            except:
                pass
            raise
    
    def press_and_release(self, key: str) -> None:
        """Press and release a single key (convenience method).
        
        Args:
            key: Key to press and release
        """
        self.press(key, presses=1)
    
    def copy(self) -> None:
        """Copy selected text (Ctrl+C / Cmd+C)."""
        logger.debug("Copying (Ctrl+C)")
        try:
            if pyautogui.platform.system() == "Darwin":
                self.hotkey("command", "c")
            else:
                self.hotkey("ctrl", "c")
        except Exception as e:
            logger.error(f"Failed to copy: {e}")
            raise
    
    def paste(self) -> None:
        """Paste clipboard content (Ctrl+V / Cmd+V)."""
        logger.debug("Pasting (Ctrl+V)")
        try:
            if pyautogui.platform.system() == "Darwin":
                self.hotkey("command", "v")
            else:
                self.hotkey("ctrl", "v")
        except Exception as e:
            logger.error(f"Failed to paste: {e}")
            raise
    
    def select_all(self) -> None:
        """Select all text (Ctrl+A / Cmd+A)."""
        logger.debug("Selecting all (Ctrl+A)")
        try:
            if pyautogui.platform.system() == "Darwin":
                self.hotkey("command", "a")
            else:
                self.hotkey("ctrl", "a")
        except Exception as e:
            logger.error(f"Failed to select all: {e}")
            raise
    
    def new_tab(self) -> None:
        """Open new tab (Ctrl+T / Cmd+T)."""
        logger.debug("Opening new tab (Ctrl+T)")
        try:
            if pyautogui.platform.system() == "Darwin":
                self.hotkey("command", "t")
            else:
                self.hotkey("ctrl", "t")
        except Exception as e:
            logger.error(f"Failed to open new tab: {e}")
            raise
    
    def close_window(self) -> None:
        """Close current window (Ctrl+W / Cmd+W)."""
        logger.debug("Closing window (Ctrl+W)")
        try:
            if pyautogui.platform.system() == "Darwin":
                self.hotkey("command", "w")
            else:
                self.hotkey("ctrl", "w")
        except Exception as e:
            logger.error(f"Failed to close window: {e}")
            raise
    
    def switch_window(self) -> None:
        """Switch to next window (Alt+Tab)."""
        logger.debug("Switching window (Alt+Tab)")
        try:
            if pyautogui.platform.system() == "Darwin":
                self.hotkey("command", "tab")
            else:
                self.hotkey("alt", "tab")
        except Exception as e:
            logger.error(f"Failed to switch window: {e}")
            raise
    
    def is_key_pressed(self, key: str) -> bool:
        """Check if a key is currently pressed.
        
        Args:
            key: Key to check
            
        Returns:
            True if key is pressed, False otherwise
        """
        try:
            return pyautogui.keyDown(key)
        except Exception as e:
            logger.error(f"Failed to check key state for '{key}': {e}")
            return False


# Global keyboard controller instance
keyboard = KeyboardController()