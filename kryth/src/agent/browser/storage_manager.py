"""Persist and restore localStorage, sessionStorage, and IndexedDB snapshots.

These are stored as a Playwright ``origins`` list (the ``origins`` key inside
``storage_state``) so they round-trip through
``BrowserContext.storage_state()`` / ``BrowserProfile(storage_state=...)``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LOCAL_STORAGE_FILE = "local_storage.json"
SESSION_STORAGE_FILE = "session_storage.json"
INDEXEDDB_SNAPSHOT_FILE = "indexeddb.json"


class StorageManager:
    """Manage web storage persistence for a single profile.

    Parameters
    ----------
    profile_dir:
        Root directory of the profile.
    """

    def __init__(self, profile_dir: Path) -> None:
        self._dir = profile_dir

    # ------------------------------------------------------------------
    # localStorage
    # ------------------------------------------------------------------

    def save_local_storage(self, origins: list[dict[str, Any]]) -> None:
        """Persist Playwright-format origins list (localStorage)."""
        self._write(LOCAL_STORAGE_FILE, origins)

    def load_local_storage(self) -> list[dict[str, Any]]:
        return self._read(LOCAL_STORAGE_FILE, default=[])

    # ------------------------------------------------------------------
    # sessionStorage
    # ------------------------------------------------------------------

    def save_session_storage(self, origins: list[dict[str, Any]]) -> None:
        self._write(SESSION_STORAGE_FILE, origins)

    def load_session_storage(self) -> list[dict[str, Any]]:
        return self._read(SESSION_STORAGE_FILE, default=[])

    # ------------------------------------------------------------------
    # IndexedDB (opaque JSON snapshot)
    # ------------------------------------------------------------------

    def save_indexeddb(self, data: Any) -> None:
        self._write(INDEXEDDB_SNAPSHOT_FILE, data)

    def load_indexeddb(self) -> Any:
        return self._read(INDEXEDDB_SNAPSHOT_FILE, default=None)

    # ------------------------------------------------------------------
    # Composite storage_state (Playwright format)
    # ------------------------------------------------------------------

    def to_storage_state(self, cookies: list[dict] | None = None) -> dict[str, Any]:
        """Return a Playwright storage_state dict with cookies + localStorage."""
        return {
            "cookies": cookies or [],
            "origins": self.load_local_storage(),
        }

    def import_storage_state(self, state: dict[str, Any]) -> None:
        """Import origins from a Playwright storage_state dict."""
        origins = state.get("origins", [])
        if origins:
            self.save_local_storage(origins)

    # ------------------------------------------------------------------
    # Clear
    # ------------------------------------------------------------------

    def clear(self) -> None:
        for filename in (LOCAL_STORAGE_FILE, SESSION_STORAGE_FILE, INDEXEDDB_SNAPSHOT_FILE):
            p = self._dir / filename
            if p.exists():
                p.unlink()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self, filename: str, data: Any) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / filename
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("Saved %s", path)

    def _read(self, filename: str, default: Any = None) -> Any:
        path = self._dir / filename
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return default
