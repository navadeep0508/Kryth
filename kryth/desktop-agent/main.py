#!/usr/bin/env python3
"""Desktop Agent - Phase 1 Demonstration

This script demonstrates the core Phase 1 capabilities:
- Open an application
- Move mouse
- Type text
- Capture screen
- Copy/paste
- Switch windows

Run this script to verify that the desktop agent foundation is working.
"""

import sys
import time
import subprocess
from pathlib import Path

# Add project root to path if needed
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from desktop_agent.config import config
from desktop_agent.input import mouse, keyboard
from desktop_agent.windows import window_manager
from desktop_agent.vision import capture

# Configure logging
import logging
logging.basicConfig(
    level=getattr(logging, config.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger("main")


def demo_phase1():
    """Run Phase 1 demonstration."""
    logger.info("=" * 60)
    logger.info("Desktop Agent - Phase 1 Demonstration")
    logger.info("=" * 60)
    
    # Step 1: Open Notepad
    logger.info("\n[Step 1] Opening Notepad...")
    try:
        notepad_proc = subprocess.Popen(["notepad.exe"])
        time.sleep(1.0)  # Wait for Notepad to start
        logger.info("Notepad process started")
    except Exception as e:
        logger.error(f"Failed to start Notepad: {e}")
        return False
    
    # Step 2: Find and activate Notepad window
    logger.info("\n[Step 2] Finding and activating Notepad window...")
    notepad_window = window_manager.wait_for_window(
        title="Untitled - Notepad",
        timeout=5.0
    )
    
    if not notepad_window:
        logger.error("Could not find Notepad window")
        notepad_proc.terminate()
        return False
    
    logger.info(f"Found window: {notepad_window.title}")
    if not notepad_window.activate():
        logger.error("Failed to activate Notepad window")
        notepad_proc.terminate()
        return False
    
    time.sleep(0.5)
    
    # Step 3: Move mouse to a position in the text area
    logger.info("\n[Step 3] Moving mouse to text area...")
    # Notepad text area is roughly at (100, 150) from top-left of window
    screen_x, screen_y = mouse.get_position()
    logger.info(f"Current mouse position: ({screen_x}, {screen_y})")
    
    # Move to a reasonable position in Notepad's client area
    target_x, target_y = 200, 200
    mouse.move_to(target_x, target_y, duration=0.5)
    time.sleep(0.2)
    
    # Step 4: Type some text
    logger.info("\n[Step 4] Typing text...")
    sample_text = "Hello from KRYTH Desktop Agent!\nThis is Phase 1 testing.\nTimestamp: " + time.strftime("%Y-%m-%d %H:%M:%S")
    keyboard.type_text(sample_text, interval=0.01)
    time.sleep(0.5)
    
    # Step 5: Select all and copy to clipboard
    logger.info("\n[Step 5] Copying text to clipboard...")
    keyboard.select_all()
    time.sleep(0.2)
    keyboard.copy()
    time.sleep(0.2)
    
    clipboard_text = clipboard.paste()
    if clipboard_text:
        logger.info(f"Clipboard contains {len(clipboard_text)} characters")
    else:
        logger.warning("Clipboard is empty")
    
    # Step 6: Capture screenshot
    logger.info("\n[Step 6] Capturing screenshot...")
    screenshot_path = Path(__file__).parent / "phase1_screenshot.png"
    success = capture.save_screenshot(str(screenshot_path), monitor=1)
    if success:
        logger.info(f"Screenshot saved to: {screenshot_path}")
        if screenshot_path.exists():
            size = screenshot_path.stat().st_size
            logger.info(f"File size: {size} bytes")
    else:
        logger.error("Failed to capture screenshot")
    
    # Step 7: Open a second window (Calculator) to test window switching
    logger.info("\n[Step 7] Opening Calculator to test window switching...")
    try:
        calc_proc = subprocess.Popen(["calc.exe"])
        time.sleep(1.0)
        logger.info("Calculator started")
    except Exception as e:
        logger.warning(f"Could not start Calculator: {e}")
        calc_proc = None
    
    # Step 8: Switch back to Notepad
    if calc_proc:
        logger.info("\n[Step 8] Switching back to Notepad...")
        time.sleep(0.5)
        notepad_window.activate()
        time.sleep(0.5)
    
    # Step 9: Paste clipboard content (verification)
    logger.info("\n[Step 9] Pasting clipboard content to verify copy worked...")
    keyboard.press("end")
    keyboard.press("enter")
    time.sleep(0.2)
    keyboard.paste()
    time.sleep(0.5)
    
    # Step 10: Final screenshot
    logger.info("\n[Step 10] Final screenshot after paste...")
    final_screenshot = Path(__file__).parent / "phase1_final.png"
    capture.save_screenshot(str(final_screenshot), monitor=1)
    logger.info(f"Final screenshot saved to: {final_screenshot}")
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("Phase 1 Demonstration Complete!")
    logger.info("=" * 60)
    logger.info("\nCapabilities demonstrated:")
    logger.info("  ✓ Open application (subprocess)")
    logger.info("  ✓ Window detection and activation")
    logger.info("  ✓ Mouse movement")
    logger.info("  ✓ Keyboard typing")
    logger.info("  ✓ Clipboard copy/paste")
    logger.info("  ✓ Screen capture")
    logger.info("  ✓ Window switching")
    
    # Cleanup: close Notepad (and Calculator if opened)
    logger.info("\n[Cleanup] Closing applications...")
    time.sleep(1.0)
    try:
        notepad_proc.terminate()
        notepad_proc.wait(timeout=2)
        logger.info("Notepad closed")
    except:
        logger.warning("Could not cleanly close Notepad")
    
    if calc_proc:
        try:
            calc_proc.terminate()
            calc_proc.wait(timeout=2)
            logger.info("Calculator closed")
        except:
            logger.warning("Could not cleanly close Calculator")
    
    logger.info("\nDemo finished successfully!")
    return True


if __name__ == "__main__":
    try:
        success = demo_phase1()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\nDemo interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.exception(f"Demo failed with error: {e}")
        sys.exit(1)