"""Screen capture module for desktop automation.

Provides screen capture functionality using mss (Multi-Screen Shot).
"""

import logging
from typing import Optional, Tuple, List, Union

import numpy as np
from PIL import Image

import mss
import mss.tools

from desktop_agent.config import config

logger = logging.getLogger(__name__)


class ScreenCapture:
    """Screen capture controller using mss."""
    
    def __init__(self):
        """Initialize the screen capture controller."""
        self._sct = mss.mss()
        self._monitors: List[Dict[str, Any]] = []
        self._refresh_monitors()
        logger.debug("ScreenCapture initialized")
    
    def _refresh_monitors(self) -> None:
        """Refresh the list of available monitors."""
        try:
            self._monitors = self._sct.monitors
            logger.debug(f"Found {len(self._monitors) - 1} monitors (monitor 0 is virtual)")
        except Exception as e:
            logger.error(f"Failed to get monitors: {e}")
            self._monitors = []
    
    def get_monitors(self) -> List[Dict[str, Any]]:
        """Get list of available monitors.
        
        Returns:
            List of monitor info dictionaries. Monitor 0 is the virtual screen
            spanning all monitors. Monitors 1+ are individual displays.
        """
        return self._monitors.copy()
    
    def get_primary_monitor(self) -> Dict[str, Any]:
        """Get the primary monitor.
        
        Returns:
            Monitor info dictionary for the primary display
        """
        if len(self._monitors) > 1:
            return self._monitors[1].copy()
        else:
            # Fallback to virtual screen
            return self._monitors[0].copy() if self._monitors else {}
    
    def capture_monitor(
        self,
        monitor_index: int = 1,
        as_numpy: bool = False
    ) -> Union[Image.Image, np.ndarray]:
        """Capture a specific monitor.
        
        Args:
            monitor_index: Monitor number (1 = primary, 0 = all monitors)
            as_numpy: Return as numpy array instead of PIL Image
            
        Returns:
            PIL Image or numpy array of the captured screen
        """
        if monitor_index >= len(self._monitors):
            raise ValueError(f"Monitor {monitor_index} not found. Available: 0-{len(self._monitors)-1}")
        
        monitor = self._monitors[monitor_index]
        logger.info(f"Capturing monitor {monitor_index}: {monitor}")
        
        try:
            screenshot = self._sct.grab(monitor)
            
            if as_numpy:
                # Convert to numpy array (BGRA format)
                img_array = np.array(screenshot)
                return img_array
            else:
                # Convert to PIL Image
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                return img
        except Exception as e:
            logger.error(f"Failed to capture monitor {monitor_index}: {e}")
            raise
    
    def capture_screen(
        self,
        region: Optional[Tuple[int, int, int, int]] = None,
        as_numpy: bool = False
    ) -> Union[Image.Image, np.ndarray]:
        """Capture the primary screen or a region.
        
        Args:
            region: Optional (left, top, width, height) tuple to capture
            as_numpy: Return as numpy array instead of PIL Image
            
        Returns:
            PIL Image or numpy array of the captured screen/region
        """
        if region:
            left, top, width, height = region
            monitor = {
                "left": left,
                "top": top,
                "width": width,
                "height": height
            }
            logger.info(f"Capturing region: {region}")
        else:
            monitor = self.get_primary_monitor()
            logger.info("Capturing primary screen")
        
        try:
            screenshot = self._sct.grab(monitor)
            
            if as_numpy:
                img_array = np.array(screenshot)
                return img_array
            else:
                img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
                return img
        except Exception as e:
            logger.error(f"Failed to capture screen: {e}")
            raise
    
    def capture_all_screens(self, as_numpy: bool = False) -> Union[Image.Image, np.ndarray]:
        """Capture all monitors as a single image.
        
        Args:
            as_numpy: Return as numpy array instead of PIL Image
            
        Returns:
            PIL Image or numpy array of all monitors combined
        """
        return self.capture_monitor(monitor_index=0, as_numpy=as_numpy)
    
    def save_screenshot(
        self,
        filepath: str,
        monitor: int = 1,
        region: Optional[Tuple[int, int, int, int]] = None,
        format: Optional[str] = None
    ) -> bool:
        """Capture and save screenshot to file.
        
        Args:
            filepath: Output file path
            monitor: Monitor index (if region not specified)
            region: Optional region to capture (overrides monitor)
            format: Image format (inferred from extension if None)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if region:
                img = self.capture_screen(region=region, as_numpy=False)
            else:
                img = self.capture_monitor(monitor_index=monitor, as_numpy=False)
            
            if format:
                img.save(filepath, format=format)
            else:
                img.save(filepath)
            
            logger.info(f"Screenshot saved to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save screenshot to {filepath}: {e}")
            return False
    
    def get_screen_size(self, monitor: int = 1) -> Tuple[int, int]:
        """Get the size of a monitor.
        
        Args:
            monitor: Monitor index
            
        Returns:
            (width, height) tuple
        """
        if monitor >= len(self._monitors):
            raise ValueError(f"Monitor {monitor} not found")
        
        mon = self._monitors[monitor]
        return (mon["width"], mon["height"])
    
    def get_screen_bounds(self, monitor: int = 1) -> Tuple[int, int, int, int]:
        """Get the bounds of a monitor.
        
        Args:
            monitor: Monitor index
            
        Returns:
            (left, top, width, height) tuple
        """
        if monitor >= len(self._monitors):
            raise ValueError(f"Monitor {monitor} not found")
        
        mon = self._monitors[monitor]
        return (mon["left"], mon["top"], mon["width"], mon["height"])
    
    def get_pixel_color(
        self,
        x: int,
        y: int,
        monitor: int = 1
    ) -> Optional[Tuple[int, int, int]]:
        """Get the color of a pixel.
        
        Args:
            x: X coordinate
            y: Y coordinate
            monitor: Monitor index
            
        Returns:
            (R, G, B) tuple, or None if out of bounds
        """
        try:
            img = self.capture_monitor(monitor_index=monitor, as_numpy=True)
            # mss returns BGRA, we want RGB
            if 0 <= x < img.shape[1] and 0 <= y < img.shape[0]:
                b, g, r, a = img[y, x]
                return (r, g, b)
        except Exception as e:
            logger.error(f"Failed to get pixel color at ({x}, {y}): {e}")
        return None
    
    def find_color(
        self,
        target_color: Tuple[int, int, int],
        tolerance: int = 0,
        monitor: int = 1
    ) -> Optional[Tuple[int, int]]:
        """Find first occurrence of a color on screen.
        
        Args:
            target_color: (R, G, B) color to find
            tolerance: Color matching tolerance (0-255)
            monitor: Monitor index
            
        Returns:
            (x, y) coordinates of first match, or None if not found
        """
        try:
            img = self.capture_monitor(monitor_index=monitor, as_numpy=True)
            # Convert to RGB if needed
            if img.shape[2] == 4:
                img = img[:, :, :3]
            
            # Create mask for color within tolerance
            lower = np.array([c - tolerance for c in target_color])
            upper = np.array([c + tolerance for c in target_color])
            mask = np.all((img >= lower) & (img <= upper), axis=2)
            
            # Find first match
            matches = np.argwhere(mask)
            if len(matches) > 0:
                y, x = matches[0]
                return (int(x), int(y))
        except Exception as e:
            logger.error(f"Failed to find color {target_color}: {e}")
        return None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def close(self) -> None:
        """Close the mss instance."""
        try:
            self._sct.close()
            logger.debug("ScreenCapture closed")
        except:
            pass
    
    def __del__(self):
        self.close()


# Global screen capture instance
capture = ScreenCapture()