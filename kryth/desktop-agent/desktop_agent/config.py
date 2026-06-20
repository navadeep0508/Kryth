"""Configuration settings for the Desktop Agent."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DesktopAgentConfig:
    """Configuration for desktop automation operations."""
    
    # Mouse settings
    mouse_move_duration: float = 0.1  # seconds for smooth mouse movement
    mouse_click_pause: float = 0.05   # pause after click
    mouse_scroll_amount: int = 5      # lines to scroll
    
    # Keyboard settings
    keyboard_type_interval: float = 0.01  # delay between keystrokes
    keyboard_hotkey_pause: float = 0.1    # pause after hotkey
    
    # Window settings
    window_focus_timeout: float = 5.0     # seconds to wait for window focus
    window_switch_delay: float = 0.2      # delay after switching windows
    
    # Screenshot settings
    screenshot_monitor: int = 1           # which monitor to capture (1 = primary)
    screenshot_format: str = "png"        # output format
    
    # Clipboard settings
    clipboard_retry_attempts: int = 3     # retry attempts for clipboard ops
    clipboard_retry_delay: float = 0.1    # delay between retries
    
    # Safety settings
    fail_safe: bool = True                # enable pyautogui fail-safe (move to corner)
    fail_safe_corner_pixels: int = 10     # corner size for fail-safe
    
    # Logging
    log_level: str = "INFO"
    log_file: Optional[str] = None
    
    def __post_init__(self):
        """Validate configuration values."""
        if self.mouse_move_duration < 0:
            raise ValueError("mouse_move_duration must be non-negative")
        if self.keyboard_type_interval < 0:
            raise ValueError("keyboard_type_interval must be non-negative")


# Global config instance
config = DesktopAgentConfig()