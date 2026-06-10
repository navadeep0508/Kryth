"""BrowserProfileManager — central API for persistent browser profiles.

Each profile is a directory containing:
- The Playwright / Chromium user-data-dir  (``chromium/``)
- Exported cookies                         (``cookies.json``)
- Exported localStorage                    (``local_storage.json``)
- Browser state snapshots                  (``snapshots/``)
- Rolling backups                          (``backups/``)
- Profile metadata                         (``kryth_profile.json``)

Global profiles live under ``~/.kryth/browser_profiles/<name>/``.
Project-specific profiles live under ``<project_root>/.kryth/browser_profiles/<name>/``.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.browser.config import (
    BROWSER_PROFILES_DIR,
    BUILTIN_PROFILE_TYPES,
    DEFAULT_PROFILE,
    PROFILE_METADATA_FILE,
    SENSITIVE_DOMAINS,
)
from agent.browser.cookie_manager import CookieManager
from agent.browser.recovery import ProfileRecovery
from agent.browser.snapshot import SnapshotManager
from agent.browser.storage_manager import StorageManager

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Profile metadata model
# ------------------------------------------------------------------


@dataclass
class ProfileMetadata:
    name: str
    profile_type: str = "default"
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    project: str = ""
    encrypted: bool = False
    description: str = ""
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileMetadata":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


# ------------------------------------------------------------------
# BrowserProfileManager
# ------------------------------------------------------------------


class BrowserProfileManager:
    """Manage browser profiles for KRYTH autonomous workflows.

    Parameters
    ----------
    project_hash:
        Optional project identifier.  When supplied, the profile is stored
        inside the project's ``.kryth/`` directory for isolation.
    base_dir:
        Override the global profiles root (mainly for testing).
    """

    def __init__(
        self,
        project_hash: str = "",
        base_dir: Optional[Path] = None,
    ) -> None:
        self.project_hash = project_hash
        if base_dir:
            self._root = base_dir
        elif project_hash:
            from agent.env import home_dir  # type: ignore[import]
            self._root = home_dir(project_hash) / "browser_profiles"
        else:
            self._root = BROWSER_PROFILES_DIR

        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def profile_dir(self, name: str) -> Path:
        """Return the root directory for profile *name*."""
        return self._root / name

    def list_profiles(self) -> list[str]:
        """Return names of all existing profiles."""
        return sorted(
            p.name for p in self._root.iterdir() if p.is_dir() and not p.name.startswith(".")
        )

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def create(
        self,
        name: str = DEFAULT_PROFILE,
        profile_type: str = "default",
        description: str = "",
        tags: list[str] | None = None,
    ) -> Path:
        """Create a new profile directory and write initial metadata.

        Returns the profile directory path.
        """
        pdir = self.profile_dir(name)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "chromium").mkdir(exist_ok=True)

        meta = ProfileMetadata(
            name=name,
            profile_type=profile_type,
            project=self.project_hash,
            description=description,
            tags=tags or [],
        )
        self._write_metadata(pdir, meta)
        logger.info("Created browser profile '%s' at %s", name, pdir)
        return pdir

    def load(self, name: str = DEFAULT_PROFILE) -> ProfileMetadata:
        """Load (or auto-create) a profile and return its metadata."""
        pdir = self.profile_dir(name)
        if not pdir.exists():
            self.create(name)
        meta = self._read_metadata(pdir)
        meta.last_used = time.time()
        self._write_metadata(pdir, meta)
        return meta

    def save(
        self,
        name: str = DEFAULT_PROFILE,
        cookies: list[dict] | None = None,
        origins: list[dict] | None = None,
    ) -> None:
        """Persist cookies and localStorage for *name* to disk."""
        pdir = self.profile_dir(name)
        pdir.mkdir(parents=True, exist_ok=True)
        if cookies is not None:
            CookieManager(pdir).save(cookies)
        if origins is not None:
            StorageManager(pdir).save_local_storage(origins)
        logger.debug("Profile '%s' saved (cookies=%s, origins=%s)", name, bool(cookies), bool(origins))

    def switch(self, name: str) -> ProfileMetadata:
        """Switch to *name*, creating it if it does not exist.

        Returns the loaded metadata.
        """
        return self.load(name)

    def backup(self, name: str = DEFAULT_PROFILE, label: str = "") -> Path:
        """Create a timestamped backup of profile *name*.

        Returns the backup directory path.
        """
        pdir = self.profile_dir(name)
        if not pdir.exists():
            raise FileNotFoundError(f"Profile '{name}' does not exist.")
        return ProfileRecovery(pdir).backup(label=label)

    def restore(self, name: str = DEFAULT_PROFILE, backup_path: Optional[Path] = None) -> bool:
        """Restore profile *name* from *backup_path* (latest if omitted).

        Returns True on success.
        """
        pdir = self.profile_dir(name)
        return ProfileRecovery(pdir).restore(backup_path=backup_path)

    def clear(self, name: str = DEFAULT_PROFILE, keep_backups: bool = True) -> None:
        """Remove all session data from profile *name*.

        If *keep_backups* is True (default), the backups/ subdirectory is
        preserved.
        """
        pdir = self.profile_dir(name)
        if not pdir.exists():
            return

        backups_dir = pdir / "backups"
        backups_snapshot: Optional[Path] = None

        if keep_backups and backups_dir.exists():
            import tempfile
            tmp = tempfile.mkdtemp(prefix="kryth-backup-preserve-")
            backups_snapshot = Path(tmp) / "backups"
            shutil.copytree(backups_dir, backups_snapshot)

        # Preserve metadata so the profile directory itself stays valid
        meta = self._read_metadata(pdir)

        shutil.rmtree(pdir)
        pdir.mkdir(parents=True)
        (pdir / "chromium").mkdir(exist_ok=True)

        if keep_backups and backups_snapshot and backups_snapshot.exists():
            shutil.copytree(backups_snapshot, backups_dir)
            shutil.rmtree(backups_snapshot.parent, ignore_errors=True)

        # Rewrite metadata with a fresh created_at
        meta.created_at = time.time()
        meta.last_used = time.time()
        self._write_metadata(pdir, meta)
        logger.info("Cleared profile '%s'", name)

    def delete(self, name: str) -> None:
        """Permanently delete profile *name* and all its data."""
        pdir = self.profile_dir(name)
        if pdir.exists():
            shutil.rmtree(pdir)
            logger.info("Deleted profile '%s'", name)

    # ------------------------------------------------------------------
    # Sub-managers (convenience accessors)
    # ------------------------------------------------------------------

    def cookies(self, name: str = DEFAULT_PROFILE) -> CookieManager:
        return CookieManager(self.profile_dir(name))

    def storage(self, name: str = DEFAULT_PROFILE) -> StorageManager:
        return StorageManager(self.profile_dir(name))

    def snapshots(self, name: str = DEFAULT_PROFILE) -> SnapshotManager:
        return SnapshotManager(self.profile_dir(name))

    def recovery(self, name: str = DEFAULT_PROFILE) -> ProfileRecovery:
        return ProfileRecovery(self.profile_dir(name))

    # ------------------------------------------------------------------
    # Playwright integration
    # ------------------------------------------------------------------

    def get_browser_profile(
        self,
        name: str = DEFAULT_PROFILE,
        headless: Optional[bool] = None,
        extra_args: list[str] | None = None,
    ) -> "BrowserProfile":  # type: ignore[name-defined]  # noqa: F821
        """Return a ``BrowserProfile`` backed by this profile's persistent dir.

        The returned object can be passed directly to ``BrowserSession``.
        """
        from agent.browser.persistent_context import make_persistent_profile

        self.load(name)  # ensure directory + metadata exist
        return make_persistent_profile(
            name,
            project_hash=self.project_hash,
            headless=headless,
            extra_args=extra_args,
        )

    def get_storage_state(self, name: str = DEFAULT_PROFILE) -> dict:
        """Return a Playwright-compatible storage_state dict for *name*."""
        pdir = self.profile_dir(name)
        cm = CookieManager(pdir)
        sm = StorageManager(pdir)
        return {
            "cookies": cm.load(),
            "origins": sm.load_local_storage(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_metadata(self, pdir: Path, meta: ProfileMetadata) -> None:
        path = pdir / PROFILE_METADATA_FILE
        path.write_text(json.dumps(meta.to_dict(), indent=2), encoding="utf-8")

    def _read_metadata(self, pdir: Path) -> ProfileMetadata:
        path = pdir / PROFILE_METADATA_FILE
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return ProfileMetadata.from_dict(data)
            except Exception:
                pass
        # Default metadata if file missing / corrupt
        return ProfileMetadata(name=pdir.name, project=self.project_hash)
