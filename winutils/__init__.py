"""Platform-specific utilities for KRYTH."""

from __future__ import annotations

import sys

__all__ = []

if sys.platform == "win32":
    from winutils.windows import (  # noqa: F401
        add_scripts_directory_to_path,
        broadcast_environment_change,
        configure_windows_path,
        get_python_scripts_directory,
        get_user_path,
        is_scripts_directory_in_path,
    )
