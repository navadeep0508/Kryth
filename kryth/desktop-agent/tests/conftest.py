"""Pytest configuration and fixtures for desktop-agent tests."""

import sys
from unittest.mock import MagicMock, patch
import pytest

# Mock problematic modules that require X server or display before they're imported
mock_modules = [
    'pyautogui',
    'pygetwindow',
    'mouseinfo',
    'Xlib',
    'Xlib.display',
    'Xlib.xauth',
]

for mod in mock_modules:
    sys.modules[mod] = MagicMock()

# Mock pyperclip with proper exception class
pyperclip_mock = MagicMock()
pyperclip_mock.PyperclipException = type('PyperclipException', (Exception,), {})
sys.modules['pyperclip'] = pyperclip_mock

# Mock mss with proper structure including tools submodule and monitor data
mss_mock = MagicMock()
mss_instance = MagicMock()
mss_instance.monitors = [
    {},  # Monitor 0 is always a virtual monitor (ignored)
    {
        'left': 0,
        'top': 0,
        'width': 1920,
        'height': 1080,
    },  # Primary monitor
]
mss_mock.mss.return_value = mss_instance
mss_mock.tools = MagicMock()
sys.modules['mss'] = mss_mock
sys.modules['mss.tools'] = mss_mock.tools

# Mock PIL modules
pil_mock = MagicMock()
pil_img_instance = MagicMock()
pil_mock.Image = MagicMock()
pil_mock.Image.frombytes.return_value = pil_img_instance
sys.modules['PIL'] = pil_mock
sys.modules['PIL.Image'] = pil_mock.Image

# Mock numpy
np_mock = MagicMock()
sys.modules['numpy'] = np_mock


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all mocks before each test."""
    for mod in mock_modules:
        if mod in sys.modules and hasattr(sys.modules[mod], 'reset_mock'):
            sys.modules[mod].reset_mock()
    if 'pyperclip' in sys.modules and hasattr(sys.modules['pyperclip'], 'reset_mock'):
        sys.modules['pyperclip'].reset_mock()
    if 'mss' in sys.modules:
        mss_instance.monitors = [
            {},
            {'left': 0, 'top': 0, 'width': 1920, 'height': 1080},
        ]
        mss_mock.mss.return_value = mss_instance