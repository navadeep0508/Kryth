"""Mouse control module for desktop automation.

Provides high-level mouse operations with error handling and logging.
"""

import logging
import time
from typing import Tuple, Optional

import pyautogui

from desktop_agent.config import config

logger = logging.getLogger(__name__)

# Configure pyautogui safety
pyautogui.FAILSAFE = config.fail_safe
pyautogui.PAUSE = config.mouse_click_pause


class MouseController:
    """Controller for mouse operations."""
    
    def __init__(self):
        """Initialize the mouse controller."""
        self.current_position: Tuple[int, int] = (0, 0)
        logger.debug("MouseController initialized")
    
    def move_to(self, x: int, y: int, duration: Optional[float] = None) -> None:
        """Move mouse to absolute coordinates.
        
        Args:
            x: X coordinate
            y: Y coordinate
            duration: Time in seconds for the movement (smooth)
        """
        if duration is None:
            duration = config.mouse_move_duration
        
        logger.info(f"Moving mouse to ({x}, {y}) over {duration:.2f}s")
        try:
            pyautogui.moveTo(x, y, duration=duration)
            self.current_position = (x, y)
        except Exception as e:
            logger.error(f"Failed to move mouse to ({x}, {y}): {e}")
            raise
    
    def move_relative(self, dx: int, dy: int, duration: Optional[float] = None) -> None:
        """Move mouse relative to current position.
        
        Args:
            dx: Change in X
            dy: Change in Y
            duration: Time in seconds for the movement
        """
        if duration is None:
            duration = config.mouse_move_duration
        
        x, y = self.current_position
        target_x, target_y = x + dx, y + dy
        logger.info(f"Moving mouse by ({dx}, {dy}) to ({target_x}, {target_y})")
        try:
            pyautogui.moveRel(dx, dy, duration=duration)
            self.current_position = (target_x, target_y)
        except Exception as e:
            logger.error(f"Failed to move mouse by ({dx}, {dy}): {e}")
            raise
    
    def click(
        self,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        clicks: int = 1,
        interval: float = 0.0
    ) -> None:
        """Click at current or specified position.
        
        Args:
            x: X coordinate (None = current position)
            y: Y coordinate (None = current position)
            button: "left", "right", or "middle"
            clicks: Number of clicks
            interval: Interval between clicks in seconds
        """
        if x is not None and y is not None:
            logger.info(f"Clicking {button} button {clicks}x at ({x}, {y})")
            try:
                pyautogui.click(x, y, button=button, clicks=clicks, interval=interval)
                self.current_position = (x, y)
            except Exception as e:
                logger.error(f"Failed to click at ({x}, {y}): {e}")
                raise
        else:
            logger.info(f"Clicking {button} button {clicks}x at current position")
            try:
                pyautogui.click(button=button, clicks=clicks, interval=interval)
            except Exception as e:
                logger.error(f"Failed to click: {e}")
                raise
    
    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        """Perform a double click.
        
        Args:
            x: X coordinate (None = current position)
            y: Y coordinate (None = current position)
        """
        self.click(x, y, clicks=2)
    
    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        """Perform a right click.
        
        Args:
            x: X coordinate (None = current position)
            y: Y coordinate (None = current position)
        """
        self.click(x, y, button="right")
    
    def scroll(
        self,
        clicks: int,
        x: Optional[int] = None,
        y: Optional[int] = None
    ) -> None:
        """Scroll the mouse wheel.
        
        Args:
            clicks: Number of scroll "clicks" (positive = up, negative = down)
            x: X coordinate to scroll at (None = current)
            y: Y coordinate to scroll at (None = current)
        """
        amount = clicks * config.mouse_scroll_amount
        logger.info(f"Scrolling by {amount} (clicks={clicks})")
        
        try:
            if x is not None and y is not None:
                pyautogui.scroll(amount, x=x, y=y)
            else:
                pyautogui.scroll(amount)
        except Exception as e:
            logger.error(f"Failed to scroll: {e}")
            raise
    
    def drag_to(
        self,
        x: int,
        y: int,
        button: str = "left",
        duration: Optional[float] = None
    ) -> None:
        """Drag from current position to target.
        
        Args:
            x: Target X coordinate
            y: Target Y coordinate
            button: Mouse button to hold
            duration: Time in seconds for the drag
        """
        if duration is None:
            duration = config.mouse_move_duration
        
        logger.info(f"Dragging from {self.current_position} to ({x}, {y})")
        try:
            pyautogui.dragTo(x, y, duration=duration, button=button)
            self.current_position = (x, y)
        except Exception as e:
            logger.error(f"Failed to drag to ({x}, {y}): {e}")
            raise
    
    def get_position(self) -> Tuple[int, int]:
        """Get current mouse position.
        
        Returns:
            (x, y) tuple
        """
        x, y = pyautogui.position()
        self.current_position = (x, y)
        return (x, y)
    
    def is_on_screen(self, x: int, y: int) -> bool:
        """Check if coordinates are within the primary screen.
        
        Args:
            x: X coordinate
            y: Y coordinate
            
        Returns:
            True if on screen, False otherwise
        """
        screen_width, screen_height = pyautogui.size()
        return 0 <= x < screen_width and 0 <= y < screen_height


# Global mouse controller instance
mouse = MouseController()