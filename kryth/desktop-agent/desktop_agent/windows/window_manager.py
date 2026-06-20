"""Window management module for desktop automation.

Provides window enumeration, focus, positioning, and metadata using
pygetwindow and psutil.
"""

import logging
import time
from typing import List, Optional, Tuple, Dict, Any

import pygetwindow as gw
import psutil

from desktop_agent.config import config

logger = logging.getLogger(__name__)


class WindowInfo:
    """Information about a window."""
    
    def __init__(self, window: gw.Window):
        """Initialize from a pygetwindow Window object."""
        self._window = window
        self._pid: Optional[int] = None
        self._title: str = window.title
        self._left: int = window.left
        self._top: int = window.top
        self._width: int = window.width
        self._height: int = window.height
    
    @property
    def title(self) -> str:
        """Window title."""
        return self._title
    
    @property
    def geometry(self) -> Tuple[int, int, int, int]:
        """Window geometry (left, top, width, height)."""
        return (self._left, self._top, self._width, self._height)
    
    @property
    def position(self) -> Tuple[int, int]:
        """Window position (left, top)."""
        return (self._left, self._top)
    
    @property
    def size(self) -> Tuple[int, int]:
        """Window size (width, height)."""
        return (self._width, self._height)
    
    @property
    def pid(self) -> Optional[int]:
        """Process ID that owns the window."""
        if self._pid is None:
            self._pid = self._get_pid()
        return self._pid
    
    @property
    def process_name(self) -> Optional[str]:
        """Name of the process that owns the window."""
        if self.pid is None:
            return None
        try:
            proc = psutil.Process(self.pid)
            return proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
    
    def is_active(self) -> bool:
        """Check if this window is currently active/focused."""
        try:
            active = gw.getActiveWindow()
            return active is not None and active._hWnd == self._window._hWnd
        except:
            return False
    
    def is_visible(self) -> bool:
        """Check if window is visible."""
        try:
            return self._window.visible
        except:
            return False
    
    def is_minimized(self) -> bool:
        """Check if window is minimized."""
        try:
            return self._window.isMinimized
        except:
            return False
    
    def is_maximized(self) -> bool:
        """Check if window is maximized."""
        try:
            return self._window.isMaximized
        except:
            return False
    
    def activate(self, timeout: Optional[float] = None) -> bool:
        """Activate/focus this window.
        
        Args:
            timeout: Maximum time to wait for activation
            
        Returns:
            True if successful, False otherwise
        """
        if timeout is None:
            timeout = config.window_focus_timeout
        
        logger.info(f"Activating window: {self._title}")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                self._window.activate()
                # Give the system a moment to switch focus
                time.sleep(0.1)
                if self.is_active():
                    logger.debug("Window activated successfully")
                    return True
            except Exception as e:
                logger.warning(f"Activation attempt failed: {e}")
                time.sleep(0.2)
        
        logger.error(f"Failed to activate window '{self._title}' after {timeout}s")
        return False
    
    def close(self) -> bool:
        """Close the window.
        
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Closing window: {self._title}")
        try:
            self._window.close()
            return True
        except Exception as e:
            logger.error(f"Failed to close window '{self._title}': {e}")
            return False
    
    def minimize(self) -> bool:
        """Minimize the window."""
        logger.info(f"Minimizing window: {self._title}")
        try:
            self._window.minimize()
            return True
        except Exception as e:
            logger.error(f"Failed to minimize window '{self._title}': {e}")
            return False
    
    def maximize(self) -> bool:
        """Maximize the window."""
        logger.info(f"Maximizing window: {self._title}")
        try:
            self._window.maximize()
            return True
        except Exception as e:
            logger.error(f"Failed to maximize window '{self._title}': {e}")
            return False
    
    def restore(self) -> bool:
        """Restore the window (from minimized/maximized)."""
        logger.info(f"Restoring window: {self._title}")
        try:
            self._window.restore()
            return True
        except Exception as e:
            logger.error(f"Failed to restore window '{self._title}': {e}")
            return False
    
    def move_to(self, x: int, y: int) -> bool:
        """Move window to position (top-left corner).
        
        Args:
            x: X coordinate
            y: Y coordinate
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Moving window '{self._title}' to ({x}, {y})")
        try:
            self._window.moveTo(x, y)
            self._left, self._top = x, y
            return True
        except Exception as e:
            logger.error(f"Failed to move window: {e}")
            return False
    
    def resize(self, width: int, height: int) -> bool:
        """Resize window.
        
        Args:
            width: New width
            height: New height
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Resizing window '{self._title}' to {width}x{height}")
        try:
            self._window.resizeTo(width, height)
            self._width, self._height = width, height
            return True
        except Exception as e:
            logger.error(f"Failed to resize window: {e}")
            return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self._title,
            "geometry": self.geometry,
            "position": self.position,
            "size": self.size,
            "pid": self.pid,
            "process_name": self.process_name,
            "is_active": self.is_active(),
            "is_visible": self.is_visible(),
            "is_minimized": self.is_minimized(),
            "is_maximized": self.is_maximized(),
        }
    
    def _get_pid(self) -> Optional[int]:
        """Get process ID for this window."""
        try:
            # pygetwindow doesn't directly provide PID, so we try to find it
            # by matching window title with process command line
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info['cmdline']
                    if cmdline and any(self._title.lower() in arg.lower() for arg in cmdline):
                        return proc.info['pid']
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.debug(f"Error getting PID: {e}")
        return None
    
    def __str__(self) -> str:
        return f"Window(title='{self._title}', geometry={self.geometry}, active={self.is_active()})"


class WindowManager:
    """Manager for desktop window operations."""
    
    def __init__(self):
        """Initialize the window manager."""
        logger.debug("WindowManager initialized")
    
    def get_all_windows(self, include_blank: bool = False) -> List[WindowInfo]:
        """Get all visible windows.
        
        Args:
            include_blank: Include windows with empty titles
            
        Returns:
            List of WindowInfo objects
        """
        try:
            windows = gw.getAllWindows()
            result = []
            for w in windows:
                if w.visible:
                    info = WindowInfo(w)
                    if info.title or include_blank:
                        result.append(info)
            return result
        except Exception as e:
            logger.error(f"Failed to get windows: {e}")
            return []
    
    def get_active_window(self) -> Optional[WindowInfo]:
        """Get the currently active/focused window.
        
        Returns:
            WindowInfo for active window, or None if no active window
        """
        try:
            active = gw.getActiveWindow()
            if active:
                return WindowInfo(active)
        except Exception as e:
            logger.debug(f"Failed to get active window: {e}")
        return None
    
    def find_window(
        self,
        title: Optional[str] = None,
        pid: Optional[int] = None,
        process_name: Optional[str] = None,
        exact: bool = False
    ) -> Optional[WindowInfo]:
        """Find a window by various criteria.
        
        Args:
            title: Window title to search for (partial match)
            pid: Process ID to match
            process_name: Process name to match
            exact: Require exact title match if using title
            
        Returns:
            First matching WindowInfo, or None if not found
        """
        windows = self.get_all_windows()
        
        for win in windows:
            if title:
                if exact:
                    if win.title == title:
                        return win
                else:
                    if title.lower() in win.title.lower():
                        return win
            if pid is not None and win.pid == pid:
                return win
            if process_name:
                if win.process_name and process_name.lower() in win.process_name.lower():
                    return win
        
        return None
    
    def find_windows(
        self,
        title: Optional[str] = None,
        pid: Optional[int] = None,
        process_name: Optional[str] = None
    ) -> List[WindowInfo]:
        """Find all windows matching criteria.
        
        Args:
            title: Window title to search for (partial match)
            pid: Process ID to match
            process_name: Process name to match
            
        Returns:
            List of matching WindowInfo objects
        """
        windows = self.get_all_windows()
        results = []
        
        for win in windows:
            match = True
            if title:
                if title.lower() not in win.title.lower():
                    match = False
            if pid is not None and win.pid != pid:
                match = False
            if process_name:
                if not win.process_name or process_name.lower() not in win.process_name.lower():
                    match = False
            
            if match:
                results.append(win)
        
        return results
    
    def wait_for_window(
        self,
        title: Optional[str] = None,
        pid: Optional[int] = None,
        process_name: Optional[str] = None,
        timeout: float = 10.0,
        interval: float = 0.5
    ) -> Optional[WindowInfo]:
        """Wait for a window to appear.
        
        Args:
            title: Wait for window with this title
            pid: Wait for window with this PID
            process_name: Wait for window from this process
            timeout: Maximum time to wait in seconds
            interval: Check interval in seconds
            
        Returns:
            WindowInfo when found, or None if timeout
        """
        logger.info(f"Waiting for window (title={title}, pid={pid}, process={process_name})")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            win = self.find_window(title=title, pid=pid, process_name=process_name)
            if win:
                elapsed = time.time() - start_time
                logger.info(f"Window found after {elapsed:.2f}s: {win.title}")
                return win
            time.sleep(interval)
        
        logger.warning(f"Window not found within {timeout}s")
        return None
    
    def switch_to(self, window: WindowInfo) -> bool:
        """Switch focus to a window.
        
        Args:
            window: WindowInfo to activate
            
        Returns:
            True if successful, False otherwise
        """
        return window.activate()
    
    def switch_by_title(
        self,
        title: str,
        wait: bool = True,
        timeout: float = 10.0
    ) -> Optional[WindowInfo]:
        """Find and switch to a window by title.
        
        Args:
            title: Window title to search for
            wait: Wait for window to appear if not found
            timeout: Timeout for waiting
            
        Returns:
            Activated WindowInfo, or None if failed
        """
        if wait:
            win = self.wait_for_window(title=title, timeout=timeout)
        else:
            win = self.find_window(title=title)
        
        if win:
            if win.activate():
                return win
        return None
    
    def close_window(self, window: WindowInfo) -> bool:
        """Close a window.
        
        Args:
            window: WindowInfo to close
            
        Returns:
            True if successful, False otherwise
        """
        return window.close()
    
    def get_window_at_position(self, x: int, y: int) -> Optional[WindowInfo]:
        """Get window at specific screen coordinates.
        
        Args:
            x: X coordinate
            y: Y coordinate
            
        Returns:
            WindowInfo at that position, or None
        """
        try:
            windows = self.get_all_windows()
            for win in windows:
                left, top, width, height = win.geometry
                if left <= x < left + width and top <= y < top + height:
                    return win
        except Exception as e:
            logger.error(f"Failed to get window at position ({x}, {y}): {e}")
        return None
    
    def get_windows_by_process(self, process_name: str) -> List[WindowInfo]:
        """Get all windows from a specific process.
        
        Args:
            process_name: Name of the process (e.g., "notepad.exe")
            
        Returns:
            List of WindowInfo objects
        """
        return self.find_windows(process_name=process_name)
    
    def list_windows(self) -> List[Dict[str, Any]]:
        """Get a list of all windows as dictionaries.
        
        Returns:
            List of window info dictionaries
        """
        windows = self.get_all_windows()
        return [w.to_dict() for w in windows]
    
    def __str__(self) -> str:
        windows = self.get_all_windows()
        return f"WindowManager: {len(windows)} windows"


# Global window manager instance
windows = WindowManager()
window_manager = windows
