"""Persistent browser profiles stored in the profiles/ directory.

Uses Playwright's ``launch_persistent_context`` so sites stay logged in
across sessions.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext


class ProfileManager:
    """Manages persistent browser profiles on disk.

    Each profile gets its own directory under ``<project_root>/.kryth/profiles/``
    and is backed by Playwright's persistent context (Chromium ``--user-data-dir``).
    """

    def __init__(self, profiles_dir: str | None = None) -> None:
        self._profiles_dir = Path(
            profiles_dir or os.path.join(".kryth", "profiles")
        ).absolute()
        self._profiles_dir.mkdir(parents=True, exist_ok=True)

    def list_profiles(self) -> list[str]:
        """Return names of all saved profiles."""
        return sorted(
            p.name for p in self._profiles_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    def profile_path(self, name: str) -> str:
        """Get the filesystem path for a named profile."""
        p = self._profiles_dir / name
        p.mkdir(parents=True, exist_ok=True)
        return str(p)

    def delete_profile(self, name: str) -> bool:
        """Delete a profile directory. Returns True on success."""
        p = self._profiles_dir / name
        if p.exists() and p.is_dir():
            shutil.rmtree(str(p))
            return True
        return False

    def profile_exists(self, name: str) -> bool:
        return (self._profiles_dir / name).is_dir()

    def duplicate_profile(self, src: str, dst: str) -> bool:
        """Copy an existing profile to a new name."""
        src_path = self._profiles_dir / src
        dst_path = self._profiles_dir / dst
        if not src_path.is_dir() or dst_path.exists():
            return False
        shutil.copytree(str(src_path), str(dst_path))
        return True

    def create_temp_profile(self) -> str:
        """Create a temporary profile directory that will be cleaned up."""
        tmp = tempfile.mkdtemp(prefix="kryth_profile_")
        return tmp

    @property
    def profiles_dir(self) -> str:
        return str(self._profiles_dir)
