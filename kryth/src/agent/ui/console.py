"""Single shared Rich Console used by every renderer.

Centralization gives us:
- One width/colour detection pass per process (no terminal flicker from
  mismatched probes).
- One target for ``Live`` / ``Progress`` lifetimes — no overlapping
  control of stdout.
- One mutex point if multiple emitters race on the bus.
- One place to fix Windows code-page issues so the rest of the codebase
  can use real Unicode glyphs without worrying about CP1252 explosions.
"""

from __future__ import annotations

import os
import sys
import threading

from rich.console import Console

from agent.env import getenv_bool
from agent.ui.theme import THEME


# On Windows the default Python stdout uses cp1252, which can't encode
# the bullet/check/arrow glyphs the renderer relies on. Reconfiguring
# stdout/stderr to UTF-8 once at import keeps the rest of the codebase
# free of encoding workarounds. errors="replace" guarantees we never
# raise during print, even on weird terminals.
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

_FORCE_TERMINAL = getenv_bool("KRYTH_FORCE_COLOR")
_NO_COLOR = os.environ.get("NO_COLOR", "") != "" or \
    getenv_bool("KRYTH_NO_COLOR")


console: Console = Console(
    theme=THEME,
    soft_wrap=False,
    force_terminal=True if _FORCE_TERMINAL else None,
    no_color=_NO_COLOR,
    highlight=False,
    # Even on Windows 10+, force the modern ANSI renderer so we never
    # fall back to the cp1252 LegacyWindowsTerm path.
    legacy_windows=False,
)


# Writers from multiple modules call print() without coordinating. A
# single re-entrant lock here lets the renderer take a critical section
# around multi-line operations (panels, diffs) without tearing.
LOCK = threading.RLock()


def width() -> int:
    return console.size.width


def is_tty() -> bool:
    return console.is_terminal
