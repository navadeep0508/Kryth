"""Windows-specific installation and PATH management utilities for KRYTH.

This module provides functions to:

* Detect the Python ``Scripts`` directory where console scripts (such as
  ``kryth.exe``) are installed.
* Read and modify the user-level ``PATH`` environment variable via the
  Windows registry (``HKEY_CURRENT_USER\\Environment``).
* Broadcast a ``WM_SETTINGCHANGE`` message so new terminal sessions pick
  up the updated ``PATH`` without requiring a reboot or admin rights.

All functions are safe to call on non-Windows platforms: they are no-ops
or return ``False`` / ``None`` when ``sys.platform != "win32``.

No external dependencies are required — only the Python standard library
(``ctypes``, ``os``, ``sys``, ``sysconfig``, ``winreg``).
"""

from __future__ import annotations

import ctypes
import os
import sys
import sysconfig
from pathlib import Path
from typing import Optional

# Registry location for the per-user environment.
_USER_ENV_REG_KEY = r"Environment"

# Windows messages / constants for broadcasting environment changes.
WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002


def _is_windows() -> bool:
    """Return True when running on Windows."""
    return sys.platform == "win32" or os.name == "nt"


def _candidate_scripts_directories() -> list[Path]:
    """Build a prioritized list of candidate Python Scripts directories.

    The order reflects where pip typically installs console scripts:

    1. ``<sys.prefix>/Scripts`` — standard / virtualenv installs.
    2. ``sysconfig.get_path("scripts")`` — the configured scripts path.
    3. Directory adjacent to ``sys.executable`` — portable Python, etc.
    4. User-level Scripts dir for ``pip install --user``:
       ``%APPDATA%\\Python\\Scripts`` on Windows.
    """
    candidates: list[Path] = []

    # 1. sys.prefix / Scripts (covers system installs and most virtualenvs).
    candidates.append(Path(sys.prefix) / "Scripts")

    # 2. sysconfig scripts path (covers conda, custom layouts, etc.).
    try:
        scripts_path = sysconfig.get_path("scripts")
        if scripts_path:
            candidates.append(Path(scripts_path))
    except Exception:
        pass

    # 3. Scripts directory next to the Python executable.
    try:
        exe_parent = Path(sys.executable).parent
        candidates.append(exe_parent / "Scripts")
    except Exception:
        pass

    # 4. User-level Scripts directory for pip install --user.
    if _is_windows():
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "Python" / "Scripts")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).lower()
        except (OSError, RuntimeError):
            key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    return unique


def get_python_scripts_directory() -> Optional[Path]:
    """Detect the Python ``Scripts`` directory where console scripts live.

    Checks multiple candidate locations and returns the first one that
    exists as a directory.  No Python version is hardcoded, so this works
    across Python 3.10 through 3.14 and future releases.

    Returns:
        The detected ``Scripts`` directory, or ``None`` if none could be
        found.
    """
    for candidate in _candidate_scripts_directories():
        try:
            if candidate.exists() and candidate.is_dir():
                return candidate
        except (OSError, RuntimeError):
            continue
    return None


def get_user_path() -> str:
    """Read the user-level ``PATH`` from the Windows registry.

    On Windows this reads ``HKEY_CURRENT_USER\\Environment\\Path``.
    On other platforms it falls back to ``os.environ["PATH"]``.

    Returns:
        The current PATH string, possibly empty.
    """
    if not _is_windows():
        return os.environ.get("PATH", "")

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _USER_ENV_REG_KEY, 0, winreg.KEY_READ
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Path")
            return value if isinstance(value, str) else ""
    except (FileNotFoundError, OSError, ImportError):
        return os.environ.get("PATH", "")


def _normalize_path_entry(entry: str) -> str:
    """Normalize a single PATH entry for case-insensitive comparison."""
    try:
        p = Path(entry)
        if p.exists():
            return str(p.resolve()).lower()
    except (OSError, RuntimeError):
        pass
    return entry.lower()


def _parse_path_entries(path_string: str) -> list[str]:
    """Split a PATH string into individual directory entries."""
    if not path_string:
        return []
    return [e.strip() for e in path_string.split(os.pathsep) if e.strip()]


def is_scripts_directory_in_path(scripts_dir: Optional[Path] = None) -> bool:
    """Check whether the Python Scripts directory is already in the user PATH.

    Args:
        scripts_dir: The directory to check.  If ``None``, it is auto-detected
            via :func:`get_python_scripts_directory`.

    Returns:
        ``True`` if the directory is already present in PATH, ``False``
        otherwise (including when no Scripts directory can be found).
    """
    if scripts_dir is None:
        scripts_dir = get_python_scripts_directory()

    if scripts_dir is None:
        return False

    try:
        scripts_normalized = str(scripts_dir.resolve()).lower()
    except (OSError, RuntimeError):
        scripts_normalized = str(scripts_dir).lower()

    for entry in _parse_path_entries(get_user_path()):
        if _normalize_path_entry(entry) == scripts_normalized:
            return True
    return False


def add_scripts_directory_to_path(scripts_dir: Optional[Path] = None) -> bool:
    """Add the Python Scripts directory to the user-level PATH.

    The change is persisted in the Windows registry
    (``HKEY_CURRENT_USER\\Environment\\Path``) so it survives reboots and
    applies to every new terminal session.  No administrator privileges are
    required.

    Args:
        scripts_dir: The directory to add.  If ``None``, it is auto-detected.

    Returns:
        ``True`` if the directory was added or was already present;
        ``False`` if the operation failed.
    """
    if not _is_windows():
        return False

    if scripts_dir is None:
        scripts_dir = get_python_scripts_directory()

    if scripts_dir is None:
        return False

    # Already configured?
    if is_scripts_directory_in_path(scripts_dir):
        return True

    try:
        scripts_dir_resolved = scripts_dir.resolve()
    except (OSError, RuntimeError):
        scripts_dir_resolved = scripts_dir

    scripts_dir_str = str(scripts_dir_resolved)

    # Read current user PATH.
    current_path = get_user_path()

    # Append the new directory (avoid trailing separator issues).
    if current_path:
        new_path = f"{current_path}{os.pathsep}{scripts_dir_str}"
    else:
        new_path = scripts_dir_str

    # Persist to the user environment registry key.
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _USER_ENV_REG_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
    except (OSError, ImportError):
        # Permission denied, registry locked, or winreg unavailable.
        return False

    # Notify Windows that the environment changed.
    broadcast_environment_change()

    return True


def broadcast_environment_change() -> bool:
    """Broadcast ``WM_SETTINGCHANGE`` so new processes see the updated PATH.

    Sends the message to all top-level windows via
    :func:`SendMessageTimeoutW`.  This is the same mechanism Windows uses
    internally when environment variables are changed through the System
    Properties dialog.

    Returns:
        ``True`` if the message was sent, ``False`` otherwise.
    """
    if not _is_windows():
        return False

    try:
        user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
    except OSError:
        return False

    try:
        result = ctypes.c_ulong(0)
        sent = user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "Environment",
            SMTO_ABORTIFHUNG,
            5000,
            ctypes.byref(result),
        )
        return sent != 0
    except Exception:
        return False


def configure_windows_path() -> dict:
    """High-level helper to configure Windows PATH for KRYTH.

    This is the entry point used by ``kryth setup`` on Windows.  It detects
    the Python Scripts directory, checks whether it is already on PATH, and
    adds it if necessary.

    Returns:
        A dict with the following keys:

        * ``success`` (``bool``) — whether PATH is now configured.
        * ``already_configured`` (``bool``) — whether it was already on PATH.
        * ``scripts_directory`` (``str | None``) — the detected directory.
        * ``message`` (``str``) — a user-facing status message.
    """
    result: dict = {
        "success": False,
        "already_configured": False,
        "scripts_directory": None,
        "message": "",
    }

    if not _is_windows():
        result["message"] = "PATH configuration is only supported on Windows."
        return result

    scripts_dir = get_python_scripts_directory()
    if scripts_dir is not None:
        result["scripts_directory"] = str(scripts_dir)

    if scripts_dir is None:
        result["message"] = "Could not detect the Python Scripts directory."
        return result

    if is_scripts_directory_in_path(scripts_dir):
        result["already_configured"] = True
        result["success"] = True
        result["message"] = "[KRYTH] PATH is already configured."
        return result

    if add_scripts_directory_to_path(scripts_dir):
        result["success"] = True
        result["message"] = (
            "[KRYTH] Installation completed successfully.\n"
            "[KRYTH] Python Scripts directory configured.\n"
            "[KRYTH] Open a new terminal and run: kryth"
        )
        return result

    result["message"] = (
        "[KRYTH] Automatic PATH configuration was unsuccessful.\n"
        "[KRYTH] Add the Python Scripts directory to your PATH manually, "
        "or run: python -m main setup --path"
    )
    return result
