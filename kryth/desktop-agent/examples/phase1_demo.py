#!/usr/bin/env python3
"""Phase 1 Example - Basic Desktop Operations

This example demonstrates individual Phase 1 capabilities in isolation.
Run this to see each module in action without the full demo sequence.
"""

import sys
import time
from pathlib import Path

# Add project root to path
if str(Path(__file__).parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from desktop_agent.config import config
from desktop_agent.input import mouse, keyboard, clipboard
from desktop_agent.windows import window_manager
from desktop_agent.vision import capture

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("phase1_demo")


def example_mouse():
    """Demonstrate mouse operations."""
    logger.info("\n--- Mouse Example ---")
    
    # Get current position
    x, y = mouse.get_position()
    logger.info(f"Current position: ({x}, {y})")
    
    # Move in a pattern
    mouse.move_to(100, 100, duration=0.5)
    time.sleep(0.2)
    mouse.move_relative(50, 50, duration=0.3)
    time.sleep(0.2)
    
    # Click
    mouse.click()
    time.sleep(0.2)
    mouse.double_click()
    time.sleep(0.2)
    mouse.right_click()
    
    logger.info("Mouse example complete")


def example_keyboard():
    """Demonstrate keyboard operations."""
    logger.info("\n--- Keyboard Example ---")
    
    # Type some text
    keyboard.type_text("Hello, Desktop Agent!", interval=0.05)
    time.sleep(0.5)
    
    # Press Enter
    keyboard.press("enter")
    time.sleep(0.2)
    
    # Hotkey example (will affect focused window)
    # keyboard.hotkey("ctrl", "a")  # Select all
    # time.sleep(0.2)
    # keyboard.hotkey("ctrl", "c")  # Copy
    
    logger.info("Keyboard example complete (typing only)")


def example_clipboard():
    """Demonstrate clipboard operations."""
    logger.info("\n--- Clipboard Example ---")
    
    # Copy text
    test_text = "KRYTH Desktop Agent clipboard test"
    success = clipboard.copy(test_text)
    logger.info(f"Copy {'succeeded' if success else 'failed'}")
    
    # Paste and verify
    pasted = clipboard.paste()
    if pasted == test_text:
        logger.info("Clipboard verification: OK")
    else:
        logger.warning(f"Clipboard mismatch. Expected: {test_text}, Got: {pasted}")
    
    # Check availability
    available = clipboard.is_available()
    logger.info(f"Clipboard available: {available}")
    
    logger.info("Clipboard example complete")


def example_windows():
    """Demonstrate window operations."""
    logger.info("\n--- Window Example ---")
    
    # List all windows
    windows = window_manager.get_all_windows()
    logger.info(f"Found {len(windows)} windows")
    
    # Show first few
    for i, win in enumerate(windows[:5]):
        logger.info(f"  {i+1}. {win.title} (PID: {win.pid}, active={win.is_active()})")
    
    # Get active window
    active = window_manager.get_active_window()
    if active:
        logger.info(f"Active window: {active.title}")
    else:
        logger.info("No active window")
    
    logger.info("Window example complete")


def example_screenshot():
    """Demonstrate screen capture."""
    logger.info("\n--- Screenshot Example ---")
    
    # Get monitor info
    monitors = capture.get_monitors()
    logger.info(f"Found {len(monitors)-1} monitors")
    
    # Capture primary monitor
    img = capture.capture_monitor(monitor_index=1)
    logger.info(f"Captured image: {img.size}, mode={img.mode}")
    
    # Save to file
    output_path = Path(__file__).parent / "example_screenshot.png"
    success = capture.save_screenshot(str(output_path), monitor=1)
    logger.info(f"Screenshot saved: {success} to {output_path}")
    
    # Get pixel color at center
    width, height = img.size
    center_color = capture.get_pixel_color(width // 2, height // 2)
    logger.info(f"Center pixel color: {center_color}")
    
    logger.info("Screenshot example complete")


def run_all_examples():
    """Run all examples sequentially."""
    logger.info("=" * 60)
    logger.info("Desktop Agent - Phase 1 Examples")
    logger.info("=" * 60)
    
    examples = [
        ("Mouse", example_mouse),
        ("Keyboard", example_keyboard),
        ("Clipboard", example_clipboard),
        ("Windows", example_windows),
        ("Screenshot", example_screenshot),
    ]
    
    for name, func in examples:
        try:
            func()
            time.sleep(0.5)  # Brief pause between examples
        except Exception as e:
            logger.error(f"Example '{name}' failed: {e}")
    
    logger.info("\n" + "=" * 60)
    logger.info("All examples completed!")
    logger.info("=" * 60)


if __name__ == "__main__":
    try:
        run_all_examples()
    except KeyboardInterrupt:
        logger.info("\nExamples interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Examples failed: {e}")
        sys.exit(1)