"""Unit tests for Phase 1 Desktop Agent modules.

These tests verify the structure and basic logic of the modules without
requiring actual desktop interaction. They use mocking for external libraries.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch


class TestConfig:
    """Tests for configuration module."""
    
    def test_config_creation(self):
        """Test that config object is created with defaults."""
        from desktop_agent.config import DesktopAgentConfig, config
        assert config is not None
        assert isinstance(config, DesktopAgentConfig)
    
    def test_config_values(self):
        """Test config has expected default values."""
        from desktop_agent.config import config
        assert config.mouse_move_duration >= 0
        assert config.keyboard_type_interval >= 0
        assert config.window_focus_timeout > 0
        assert config.screenshot_monitor >= 1
        assert config.clipboard_retry_attempts > 0
        assert config.fail_safe in (True, False)
    
    def test_config_validation(self):
        """Test config validation."""
        from desktop_agent.config import DesktopAgentConfig
        with pytest.raises(ValueError):
            DesktopAgentConfig(mouse_move_duration=-1)
        with pytest.raises(ValueError):
            DesktopAgentConfig(keyboard_type_interval=-0.5)


class TestMouseController:
    """Tests for mouse controller."""
    
    def test_mouse_controller_exists(self):
        """Test mouse controller instance exists."""
        from desktop_agent.input.mouse import MouseController, mouse
        assert mouse is not None
        assert isinstance(mouse, MouseController)
    
    def test_mouse_position_tracking(self):
        """Test position tracking."""
        from desktop_agent.input.mouse import MouseController
        m = MouseController()
        with patch('pyautogui.position', return_value=(100, 200)):
            pos = m.get_position()
            assert pos == (100, 200)
            assert m.current_position == (100, 200)
    
    def test_move_to(self):
        """Test move_to method."""
        from desktop_agent.input.mouse import MouseController
        m = MouseController()
        with patch('pyautogui.moveTo') as mock_move:
            m.move_to(500, 300, duration=0.5)
            mock_move.assert_called_once_with(500, 300, duration=0.5)
            assert m.current_position == (500, 300)
    
    def test_click(self):
        """Test click method."""
        from desktop_agent.input.mouse import MouseController
        m = MouseController()
        with patch('pyautogui.click') as mock_click:
            m.click(100, 200, button="right", clicks=2)
            mock_click.assert_called_once_with(100, 200, button="right", clicks=2, interval=0.0)
    
    def test_scroll(self):
        """Test scroll method."""
        from desktop_agent.input.mouse import MouseController
        m = MouseController()
        with patch('pyautogui.scroll') as mock_scroll:
            m.scroll(3, x=100, y=200)
            mock_scroll.assert_called_once_with(15, x=100, y=200)
    
    def test_is_on_screen(self):
        """Test screen bounds checking."""
        from desktop_agent.input.mouse import MouseController
        m = MouseController()
        with patch('pyautogui.size', return_value=(1920, 1080)):
            assert m.is_on_screen(100, 100) is True
            assert m.is_on_screen(2000, 100) is False
            assert m.is_on_screen(-10, 100) is False


class TestKeyboardController:
    """Tests for keyboard controller."""
    
    def test_keyboard_controller_exists(self):
        """Test keyboard controller instance exists."""
        from desktop_agent.input.keyboard import KeyboardController, keyboard
        assert keyboard is not None
        assert isinstance(keyboard, KeyboardController)
    
    def test_type_text(self):
        """Test type_text method."""
        from desktop_agent.input.keyboard import KeyboardController
        k = KeyboardController()
        with patch('pyautogui.typewrite') as mock_type:
            k.type_text("Hello", interval=0.02)
            mock_type.assert_called_once_with("Hello", interval=0.02)
    
    def test_press(self):
        """Test press method."""
        from desktop_agent.input.keyboard import KeyboardController
        k = KeyboardController()
        with patch('pyautogui.press') as mock_press:
            k.press("enter", presses=2)
            mock_press.assert_called_once_with("enter", presses=2, interval=0.0)
    
    def test_hotkey(self):
        """Test hotkey method."""
        from desktop_agent.input.keyboard import KeyboardController
        k = KeyboardController()
        with patch('pyautogui.hotkey') as mock_hotkey:
            k.hotkey("ctrl", "c")
            mock_hotkey.assert_called_once_with("ctrl", "c", interval=0.0)
    
    def test_copy_paste_methods(self):
        """Test copy and paste convenience methods."""
        from desktop_agent.input.keyboard import KeyboardController
        k = KeyboardController()
        with patch('pyautogui.hotkey') as mock_hotkey:
            k.copy()
            mock_hotkey.assert_called()
            # Should be called with ctrl+c (or cmd+c on Mac)
            args = mock_hotkey.call_args[0]
            assert "c" in args
            
            mock_hotkey.reset_mock()
            k.paste()
            args = mock_hotkey.call_args[0]
            assert "v" in args


class TestClipboardController:
    """Tests for clipboard controller."""
    
    def test_clipboard_controller_exists(self):
        """Test clipboard controller instance exists."""
        from desktop_agent.input.clipboard import ClipboardController, clipboard
        assert clipboard is not None
        assert isinstance(clipboard, ClipboardController)
    
    def test_copy_paste_roundtrip(self):
        """Test copy and paste work together."""
        from desktop_agent.input.clipboard import ClipboardController
        c = ClipboardController()
        with patch('pyperclip.copy') as mock_copy, \
             patch('pyperclip.paste', return_value="test text"):
            result = c.copy("test text")
            assert result is True
            mock_copy.assert_called_once_with("test text")
            
            result = c.paste()
            assert result == "test text"
    
    def test_copy_retry(self):
        """Test copy retry logic."""
        from desktop_agent.input.clipboard import ClipboardController
        import pyperclip
        c = ClipboardController()
        with patch('pyperclip.copy', side_effect=[pyperclip.PyperclipException, pyperclip.PyperclipException, True]), \
             patch.object(c, 'paste', return_value="test"):
            result = c.copy("test", retry=True)
            assert result is True
    
    def test_clear(self):
        """Test clipboard clear."""
        from desktop_agent.input.clipboard import ClipboardController
        c = ClipboardController()
        with patch.object(c, 'copy', return_value=True) as mock_copy:
            result = c.clear()
            assert result is True
            mock_copy.assert_called_with("")
    
    def test_contains(self):
        """Test clipboard contains check."""
        from desktop_agent.input.clipboard import ClipboardController
        c = ClipboardController()
        with patch('pyperclip.paste', return_value="hello world"):
            assert c.contains("world") is True
            assert c.contains("foo") is False
    
    def test_is_available(self):
        """Test clipboard availability check."""
        from desktop_agent.input.clipboard import ClipboardController
        c = ClipboardController()
        test_text = "kryth_clipboard_test"
        with patch.object(c, 'copy', return_value=True), \
             patch.object(c, 'paste', return_value=test_text):
            assert c.is_available() is True
        
        with patch.object(c, 'copy', side_effect=Exception), \
             patch.object(c, 'paste', return_value=None):
            assert c.is_available() is False


class TestWindowInfo:
    """Tests for WindowInfo class."""
    
    def test_window_info_creation(self):
        """Test WindowInfo creation from mock window."""
        from desktop_agent.windows.window_manager import WindowInfo
        mock_window = MagicMock()
        mock_window.title = "Test Window"
        mock_window.left = 100
        mock_window.top = 200
        mock_window.width = 800
        mock_window.height = 600
        mock_window._hWnd = 12345
        
        win = WindowInfo(mock_window)
        assert win.title == "Test Window"
        assert win.geometry == (100, 200, 800, 600)
        assert win.position == (100, 200)
        assert win.size == (800, 600)
    
    def test_window_info_to_dict(self):
        """Test WindowInfo serialization."""
        from desktop_agent.windows.window_manager import WindowInfo
        mock_window = MagicMock()
        mock_window.title = "Test"
        mock_window.left = 0
        mock_window.top = 0
        mock_window.width = 100
        mock_window.height = 100
        mock_window._hWnd = 123
        mock_window.visible = True
        mock_window.isMinimized = False
        mock_window.isMaximized = False
        
        win = WindowInfo(mock_window)
        d = win.to_dict()
        
        assert "title" in d
        assert "geometry" in d
        assert "is_active" in d
        assert "is_visible" in d


class TestWindowManager:
    """Tests for window manager."""
    
    def test_window_manager_exists(self):
        """Test window manager instance exists."""
        from desktop_agent.windows.window_manager import WindowManager, window_manager
        assert window_manager is not None
        assert isinstance(window_manager, WindowManager)
    
    def test_get_all_windows(self):
        """Test getting all windows."""
        from desktop_agent.windows.window_manager import WindowInfo, WindowManager
        mock_win = MagicMock()
        mock_win.visible = True
        mock_win.title = "Test"
        mock_win.left = 0
        mock_win.top = 0
        mock_win.width = 100
        mock_win.height = 100
        mock_win._hWnd = 123
        
        wm = WindowManager()
        with patch('pygetwindow.getAllWindows', return_value=[mock_win]):
            windows = wm.get_all_windows()
            assert len(windows) == 1
            assert windows[0].title == "Test"
    
    def test_find_window_by_title(self):
        """Test finding window by title."""
        from desktop_agent.windows.window_manager import WindowInfo, WindowManager
        mock_win = MagicMock()
        mock_win.title = "Notepad - Test"
        mock_win.visible = True
        mock_win.left = 0
        mock_win.top = 0
        mock_win.width = 100
        mock_win.height = 100
        mock_win._hWnd = 123
        
        wm = WindowManager()
        with patch.object(wm, 'get_all_windows', return_value=[WindowInfo(mock_win)]):
            win = wm.find_window(title="notepad")
            assert win is not None
            assert "Notepad" in win.title
    
    def test_wait_for_window(self):
        """Test waiting for window."""
        from desktop_agent.windows.window_manager import WindowInfo, WindowManager
        mock_win = MagicMock()
        mock_win.title = "Test"
        mock_win.visible = True
        mock_win.left = 0
        mock_win.top = 0
        mock_win.width = 100
        mock_win.height = 100
        mock_win._hWnd = 123
        
        wm = WindowManager()
        with patch.object(wm, 'find_window', side_effect=[None, WindowInfo(mock_win)]):
            win = wm.wait_for_window(title="Test", timeout=5.0, interval=0.1)
            assert win is not None


class TestScreenCapture:
    """Tests for screen capture."""
    
    def test_screen_capture_exists(self):
        """Test screen capture instance exists."""
        from desktop_agent.vision.capture import ScreenCapture, capture
        assert capture is not None
        assert isinstance(capture, ScreenCapture)
    
    def test_get_monitors(self):
        """Test getting monitor list."""
        from desktop_agent.vision.capture import ScreenCapture
        # Should have at least the virtual monitor
        sc = ScreenCapture()
        assert len(sc._monitors) > 0
    
    def test_get_primary_monitor(self):
        """Test getting primary monitor."""
        from desktop_agent.vision.capture import ScreenCapture
        sc = ScreenCapture()
        primary = sc.get_primary_monitor()
        assert "width" in primary
        assert "height" in primary
    
    def test_get_screen_size(self):
        """Test getting screen size."""
        from desktop_agent.vision.capture import ScreenCapture
        sc = ScreenCapture()
        width, height = sc.get_screen_size(monitor=1)
        assert width > 0
        assert height > 0
    
    def test_capture_screen_mocked(self):
        """Test screen capture with mocked mss."""
        from desktop_agent.vision.capture import ScreenCapture
        sc = ScreenCapture()
        with patch('mss.mss') as mock_mss:
            mock_screenshot = MagicMock()
            mock_screenshot.size = (1920, 1080)
            mock_screenshot.rgb = b"fake_rgb_data"
            mock_mss.return_value.grab.return_value = mock_screenshot
            
            with patch('PIL.Image.frombytes') as mock_img:
                mock_img_instance = MagicMock()
                mock_img.return_value = mock_img_instance
                
                img = sc.capture_screen()
                mock_img.assert_called_once()
                assert img == mock_img_instance
    
    def test_save_screenshot(self):
        """Test saving screenshot."""
        from desktop_agent.vision.capture import ScreenCapture
        sc = ScreenCapture()
        with patch.object(sc, 'capture_monitor') as mock_capture:
            mock_img = MagicMock()
            mock_capture.return_value = mock_img
            
            result = sc.save_screenshot("test.png", monitor=1)
            assert result is True
            # When format=None, img.save(filepath) is called without format kwarg
            mock_img.save.assert_called_once_with("test.png")
            
    def test_save_screenshot_with_format(self):
        """Test saving screenshot with explicit format."""
        from desktop_agent.vision.capture import ScreenCapture
        sc = ScreenCapture()
        with patch.object(sc, 'capture_monitor') as mock_capture:
            mock_img = MagicMock()
            mock_capture.return_value = mock_img
            
            result = sc.save_screenshot("test.jpg", format="JPEG")
            assert result is True
            mock_img.save.assert_called_once_with("test.jpg", format="JPEG")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])