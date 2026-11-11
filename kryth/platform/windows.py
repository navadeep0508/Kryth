"""Windows user PATH management for KRYTH installations."""

from __future__ import annotations

import ctypes
from importlib import metadata
import ntpath
import os
import re
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ENVIRONMENT_KEY = r"Environment"
WM_SETTINGCHANGE = 0x001A
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002


@dataclass(frozen=True)
class WindowsPathResult:
    """Result of configuring KRYTH's per-user Scripts path."""

    success: bool
    already_configured: bool
    scripts_directory: str | None
    message: str


def _is_windows() -> bool:
    return sys.platform == "win32" or os.name == "nt"


def _load_winreg() -> Any | None:
    try:
        import winreg
    except ImportError:
        return None
    return winreg


def _sysconfig_path(name: str, scheme: str | None = None) -> Path | None:
    try:
        value = sysconfig.get_path(name, scheme=scheme) if scheme else sysconfig.get_path(name)
    except (KeyError, TypeError, ValueError):
        return None
    return Path(value) if value else None


def _user_schemes() -> list[str]:
    try:
        names = sysconfig.get_scheme_names()
    except AttributeError:
        return []
    return [name for name in names if "user" in name.lower() and "nt" in name.lower()]


def _kryth_package_locations() -> list[str]:
    locations: list[str] = []
    module = sys.modules.get("kryth")
    module_file = getattr(module, "__file__", None)
    if module_file:
        locations.append(str(module_file))

    # The current directory may shadow the installed package when recovering
    # from inside a KRYTH source checkout. Installed metadata still identifies
    # the wheel's real package location in that case.
    try:
        distribution = metadata.distribution("kryth")
        installed_file = distribution.locate_file("kryth/__init__.py")
        if Path(installed_file).is_file():
            locations.append(str(installed_file))
    except metadata.PackageNotFoundError:
        pass

    return locations


def _module_uses_user_scheme(user_schemes: list[str]) -> bool:
    module_paths = [
        ntpath.normcase(ntpath.normpath(path)) for path in _kryth_package_locations()
    ]
    if not module_paths:
        return False

    for scheme in user_schemes:
        purelib = _sysconfig_path("purelib", scheme)
        if purelib is None:
            continue
        root = ntpath.normcase(ntpath.normpath(str(purelib)))
        for module_path in module_paths:
            try:
                if ntpath.commonpath((module_path, root)) == root:
                    return True
            except ValueError:
                continue
    return False


def get_scripts_directory() -> Path | None:
    """Find the Scripts directory associated with the active KRYTH install.

    A running ``kryth.exe`` is authoritative. For ``python -m kryth``, the
    loaded package's site-packages location distinguishes a user install from
    a machine/virtual-environment install. ``sysconfig`` supplies both paths,
    with the active interpreter's default scheme as the fallback.
    """
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 and argv0.name.casefold() in {"kryth.exe", "kryth-script.py"}:
        resolved_argv0 = argv0.resolve()
        if resolved_argv0.is_file():
            return resolved_argv0.parent

    user_schemes = _user_schemes()
    user_first = _module_uses_user_scheme(user_schemes)
    user_scripts = [path for scheme in user_schemes if (path := _sysconfig_path("scripts", scheme))]
    default_scripts = _sysconfig_path("scripts")

    ordered = user_scripts + ([default_scripts] if default_scripts else []) if user_first else (
        ([default_scripts] if default_scripts else []) + user_scripts
    )

    # Prefer a candidate that contains this installation's generated launcher.
    # This resolves --user installs even when a different Python is also present.
    for directory in ordered:
        if directory is not None and (directory / "kryth.exe").is_file():
            return directory

    return default_scripts or (user_scripts[0] if user_scripts else None)


def _expand_windows_variables(value: str) -> str:
    env = {key.casefold(): item for key, item in os.environ.items()}

    def replace(match: re.Match[str]) -> str:
        return env.get(match.group(1).casefold(), match.group(0))

    return re.sub(r"%([^%]+)%", replace, value)


def _normalize_path_entry(value: str) -> str:
    expanded = _expand_windows_variables(value.strip().strip('"'))
    return ntpath.normcase(ntpath.normpath(expanded))


def _path_entries(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(";") if entry.strip()]


def _read_user_path(registry: Any) -> tuple[str, int | None]:
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, ENVIRONMENT_KEY, 0, registry.KEY_READ) as key:
            try:
                value, value_type = registry.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return "", None
    except OSError:
        return "", None
    return (value if isinstance(value, str) else ""), value_type


def is_scripts_directory_in_user_path(scripts_directory: Path | str) -> bool:
    r"""Return whether the per-user registry PATH already contains Scripts."""
    registry = _load_winreg()
    if registry is None or not _is_windows():
        return False

    user_path, _ = _read_user_path(registry)
    target = _normalize_path_entry(str(scripts_directory))
    return any(_normalize_path_entry(entry) == target for entry in _path_entries(user_path))


def add_scripts_directory_to_user_path(scripts_directory: Path | str) -> bool:
    r"""Persist the Scripts directory in HKCU\Environment\Path.

    Existing per-user values and their registry type are preserved. The
    function does not copy the inherited machine PATH into the user value.
    """
    if not _is_windows():
        return False
    registry = _load_winreg()
    if registry is None:
        return False

    scripts = str(scripts_directory)
    user_path, old_type = _read_user_path(registry)
    target = _normalize_path_entry(scripts)
    if any(_normalize_path_entry(entry) == target for entry in _path_entries(user_path)):
        return True

    new_path = f"{user_path.rstrip(';')};{scripts}" if user_path.rstrip(";") else scripts
    try:
        try:
            key_context = registry.OpenKey(
                registry.HKEY_CURRENT_USER,
                ENVIRONMENT_KEY,
                0,
                registry.KEY_SET_VALUE,
            )
        except FileNotFoundError:
            create_key = getattr(registry, "CreateKeyEx", None)
            if create_key is None:
                return False
            key_context = create_key(
                registry.HKEY_CURRENT_USER,
                ENVIRONMENT_KEY,
                0,
                registry.KEY_SET_VALUE,
            )

        with key_context as key:
            value_type = old_type if old_type is not None else registry.REG_EXPAND_SZ
            registry.SetValueEx(key, "Path", 0, value_type, new_path)
    except OSError:
        return False

    broadcast_environment_change()
    return True


def broadcast_environment_change() -> bool:
    """Notify top-level Windows apps that the user's environment changed."""
    if not _is_windows():
        return False
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32.dll", use_last_error=True)
        send_message = user32.SendMessageTimeoutW
        send_message.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPCWSTR,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        )
        send_message.restype = ctypes.c_size_t
        result = ctypes.c_size_t()
        return bool(
            send_message(
                HWND_BROADCAST,
                WM_SETTINGCHANGE,
                0,
                "Environment",
                SMTO_ABORTIFHUNG,
                5000,
                ctypes.byref(result),
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def configure_windows_path() -> WindowsPathResult:
    """Configure the active KRYTH installation's Scripts directory."""
    if not _is_windows():
        return WindowsPathResult(False, False, None, "PATH configuration is only supported on Windows.")

    scripts = get_scripts_directory()
    if scripts is None:
        return WindowsPathResult(False, False, None, "Could not detect this Python installation's Scripts directory.")
    if not (scripts / "kryth.exe").is_file():
        return WindowsPathResult(
            False,
            False,
            str(scripts),
            f"KRYTH's launcher was not found in {scripts}. Install the wheel for this Python first.",
        )

    if is_scripts_directory_in_user_path(scripts):
        return WindowsPathResult(True, True, str(scripts), "KRYTH's Scripts directory is already in the user PATH.")

    if not add_scripts_directory_to_user_path(scripts):
        return WindowsPathResult(
            False,
            False,
            str(scripts),
            f"Could not update the user PATH. Add this directory manually: {scripts}",
        )

    return WindowsPathResult(
        True,
        False,
        str(scripts),
        f"Added {scripts} to the user PATH. Open a new terminal to run `kryth`.",
    )
