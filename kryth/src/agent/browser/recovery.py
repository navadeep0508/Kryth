"""Browser profile backup, restore, and corruption recovery."""

from __future__ import annotations

import json
import logging
import shutil
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Optional

from agent.browser.config import (
    BACKUPS_DIR,
    MAX_BACKUPS,
    PROFILE_METADATA_FILE,
    PROFILE_TRANSIENT_PATTERNS,
)

logger = logging.getLogger(__name__)


def _skip_transient(src: str, names: list[str]) -> set[str]:
    return {n for n in names if any(fnmatch(n, p) for p in PROFILE_TRANSIENT_PATTERNS)}


class ProfileRecovery:
    """Manage backups and restoration of a single browser profile directory.

    Parameters
    ----------
    profile_dir:
        Root directory of the profile (e.g. ``~/.kryth/browser_profiles/default/``).
    max_backups:
        Maximum number of rolling backups to keep.
    """

    def __init__(self, profile_dir: Path, max_backups: int = MAX_BACKUPS) -> None:
        self.profile_dir = profile_dir
        self.max_backups = max_backups
        self._backups_dir = profile_dir / BACKUPS_DIR

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    def backup(self, label: str = "") -> Path:
        """Create a timestamped backup of the profile.

        Returns the path of the created backup directory.
        """
        self._backups_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        name = f"backup_{ts}_{label}" if label else f"backup_{ts}"
        dest = self._backups_dir / name

        try:
            shutil.copytree(
                self.profile_dir,
                dest,
                ignore=lambda s, ns: _skip_transient(s, ns) | {BACKUPS_DIR},
            )
            logger.info("Profile backed up → %s", dest)
        except Exception as exc:
            logger.warning("Backup failed: %s", exc)
            raise

        self._prune_old_backups()
        return dest

    def list_backups(self) -> list[Path]:
        """Return backup paths sorted oldest → newest."""
        if not self._backups_dir.exists():
            return []
        entries = sorted(self._backups_dir.iterdir(), key=lambda p: p.stat().st_mtime)
        return [e for e in entries if e.is_dir()]

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(self, backup_path: Optional[Path] = None) -> bool:
        """Restore from *backup_path* (defaults to the most recent backup).

        Returns True on success.
        """
        if backup_path is None:
            backups = self.list_backups()
            if not backups:
                logger.warning("No backups available for %s", self.profile_dir)
                return False
            backup_path = backups[-1]

        if not backup_path.exists():
            logger.error("Backup path does not exist: %s", backup_path)
            return False

        # The backup may live inside profile_dir/backups/; copy it to a temp
        # location first so shutil.rmtree(profile_dir) doesn't destroy it.
        import tempfile
        tmp_staging = Path(tempfile.mkdtemp(prefix="kryth-restore-staging-"))
        staged_backup = tmp_staging / "backup"
        staged_backups_dir = tmp_staging / "all_backups"

        try:
            shutil.copytree(backup_path, staged_backup)

            # Preserve the full backups directory so we keep the history
            if self._backups_dir.exists():
                shutil.copytree(self._backups_dir, staged_backups_dir)

            if self.profile_dir.exists():
                shutil.rmtree(self.profile_dir)

            shutil.copytree(staged_backup, self.profile_dir)

            # Restore the backups directory
            if staged_backups_dir.exists():
                restored_backups = self.profile_dir / BACKUPS_DIR
                if not restored_backups.exists():
                    shutil.copytree(staged_backups_dir, restored_backups)

            logger.info("Profile restored from %s", backup_path)
            return True
        except Exception as exc:
            logger.error("Restore failed: %s", exc)
            return False
        finally:
            shutil.rmtree(tmp_staging, ignore_errors=True)

    # ------------------------------------------------------------------
    # Corruption detection
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Quick sanity check: metadata file present and readable."""
        meta = self.profile_dir / PROFILE_METADATA_FILE
        if not meta.exists():
            return False
        try:
            json.loads(meta.read_text(encoding="utf-8"))
            return True
        except Exception:
            return False

    def ensure_healthy(self) -> bool:
        """If the profile looks corrupted, restore from latest backup.

        Returns True if healthy (either already was or recovered successfully).
        """
        if self.is_healthy():
            return True
        logger.warning("Profile %s appears corrupted — attempting recovery.", self.profile_dir)
        return self.restore()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prune_old_backups(self) -> None:
        backups = self.list_backups()
        while len(backups) > self.max_backups:
            oldest = backups.pop(0)
            shutil.rmtree(oldest, ignore_errors=True)
            logger.debug("Pruned old backup: %s", oldest)
