"""Clipboard operations module for desktop automation.

Provides cross-platform clipboard access with retry logic.
"""

import logging
import time
from typing import Optional

import pyperclip
from pyperclip import PyperclipException

from desktop_agent.config import config

logger = logging.getLogger(__name__)


class ClipboardController:
    """Controller for clipboard operations."""
    
    def __init__(self):
        """Initialize the clipboard controller."""
        self._last_copied: Optional[str] = None
        logger.debug("ClipboardController initialized")
    
    def copy(self, text: str, retry: bool = True) -> bool:
        """Copy text to clipboard.
        
        Args:
            text: Text to copy
            retry: Whether to retry on failure
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"Copying text to clipboard (length={len(text)})")
        
        attempts = config.clipboard_retry_attempts if retry else 1
        for attempt in range(attempts):
            try:
                pyperclip.copy(text)
                self._last_copied = text
                logger.debug("Copy successful")
                return True
            except PyperclipException as e:
                logger.warning(f"Copy attempt {attempt + 1} failed: {e}")
                if attempt < attempts - 1:
                    time.sleep(config.clipboard_retry_delay)
                else:
                    logger.error(f"Copy failed after {attempts} attempts")
                    return False
            except Exception as e:
                logger.error(f"Unexpected error during copy: {e}")
                return False
        
        return False
    
    def paste(self) -> Optional[str]:
        """Paste text from clipboard.
        
        Returns:
            Clipboard text, or None if failed
        """
        logger.debug("Pasting text from clipboard")
        attempts = config.clipboard_retry_attempts
        for attempt in range(attempts):
            try:
                text = pyperclip.paste()
                logger.debug(f"Paste successful (length={len(text) if text else 0})")
                return text
            except PyperclipException as e:
                logger.warning(f"Paste attempt {attempt + 1} failed: {e}")
                if attempt < attempts - 1:
                    time.sleep(config.clipboard_retry_delay)
                else:
                    logger.error(f"Paste failed after {attempts} attempts")
                    return None
            except Exception as e:
                logger.error(f"Unexpected error during paste: {e}")
                return None
        
        return None
    
    def clear(self) -> bool:
        """Clear clipboard contents.
        
        Returns:
            True if successful, False otherwise
        """
        logger.debug("Clearing clipboard")
        return self.copy("")
    
    def get_text(self) -> Optional[str]:
        """Alias for paste() - get current clipboard text."""
        return self.paste()
    
    def set_text(self, text: str) -> bool:
        """Alias for copy() - set clipboard text.
        
        Args:
            text: Text to copy to clipboard
            
        Returns:
            True if successful, False otherwise
        """
        return self.copy(text)
    
    def append(self, text: str, separator: str = "\n") -> bool:
        """Append text to existing clipboard content.
        
        Args:
            text: Text to append
            separator: Separator between existing and new text
            
        Returns:
            True if successful, False otherwise
        """
        current = self.paste()
        if current is None:
            return self.copy(text)
        
        combined = current + separator + text
        return self.copy(combined)
    
    def contains(self, substring: str) -> bool:
        """Check if clipboard contains specific text.
        
        Args:
            substring: Text to search for
            
        Returns:
            True if found, False otherwise
        """
        text = self.paste()
        if text is None:
            return False
        return substring in text
    
    def get_last_copied(self) -> Optional[str]:
        """Get the last text that was successfully copied.
        
        Returns:
            Last copied text, or None if nothing copied yet
        """
        return self._last_copied
    
    def is_available(self) -> bool:
        """Check if clipboard operations are available.
        
        Returns:
            True if clipboard is accessible, False otherwise
        """
        try:
            # Try a simple copy/paste cycle
            test_text = "kryth_clipboard_test"
            self.copy(test_text, retry=False)
            result = self.paste()
            return result == test_text
        except Exception:
            return False


# Global clipboard controller instance
clipboard = ClipboardController()