"""Cross-platform single-key reader for interactive prompts.

``read_key()`` blocks until one keystroke is available and returns a
normalized token:

    'UP' / 'DOWN' / 'LEFT' / 'RIGHT'
    'ENTER'
    'ESC'
    'CTRL_C'
    'TAB'
    '' for unknown escape sequences
    otherwise the literal character, lowercased

The Windows path uses ``msvcrt.getwch``; the POSIX path puts the tty in
cbreak mode for the duration of the read and uses ``select`` to
distinguish a bare ESC from an arrow-key CSI sequence.
"""

from __future__ import annotations

import sys


def _read_key_windows() -> str:
    import msvcrt

    ch = msvcrt.getwch()
    # Arrow keys arrive as a two-byte sequence: \x00 or \xe0 then a code.
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {
            "H": "UP", "P": "DOWN",
            "K": "LEFT", "M": "RIGHT",
        }.get(code, "")
    if ch in ("\r", "\n"):
        return "ENTER"
    if ch == "\x1b":
        return "ESC"
    if ch == "\x03":
        return "CTRL_C"
    if ch == "\t":
        return "TAB"
    return ch.lower()


def _read_key_posix() -> str:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            # Could be a lone ESC or the start of a CSI sequence. Wait
            # briefly for the rest of the sequence; if nothing follows
            # it's a real ESC press.
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if not ready:
                return "ESC"
            ch2 = sys.stdin.read(1)
            if ch2 != "[":
                return "ESC"
            ch3 = sys.stdin.read(1)
            return {
                "A": "UP", "B": "DOWN",
                "C": "RIGHT", "D": "LEFT",
            }.get(ch3, "")
        if ch in ("\r", "\n"):
            return "ENTER"
        if ch == "\x03":
            return "CTRL_C"
        if ch == "\t":
            return "TAB"
        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key() -> str:
    if sys.platform == "win32":
        return _read_key_windows()
    return _read_key_posix()
