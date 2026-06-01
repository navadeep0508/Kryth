"""Terminal I/O helpers.

Interactive prompts (yes/no) live here. Output goes through the ui
package so the visual style is consistent with the rest of the agent.
"""

from __future__ import annotations

import sys

from agent import ui
from agent.env import getenv_bool


_FORCE_YES_ENV = "KRYTH_ASSUME_YES"


def _force_yes() -> bool:
    return getenv_bool(_FORCE_YES_ENV)


def confirm(message: str, default: bool = False) -> bool:
    """Prompt the user for a yes/no answer.

    - Returns ``default`` when stdin is not a TTY (CI, piped input).
    - Returns ``True`` immediately if ``KRYTH_ASSUME_YES=1``
      or the previous ``XEROCODEAI_ASSUME_YES=1`` alias is set.
    - Treats Ctrl+C / EOF as "no" without raising.
    """
    if _force_yes():
        return True

    if not sys.stdin.isatty():
        return default

    suffix = " [Y/n]" if default else " [y/N]"
    try:
        answer = input(f"{message}{suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ui.muted("(cancelled)")
        return False

    if not answer:
        return default
    return answer.startswith("y")


def info(text: str) -> None:
    ui.info(text)


def warn(text: str) -> None:
    ui.warn(text)


def error(text: str) -> None:
    ui.error(text)
