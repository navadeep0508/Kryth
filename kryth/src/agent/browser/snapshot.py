"""Browser state snapshots — save and restore complete browser state.

A snapshot captures:
- All cookies (Playwright storage_state format)
- localStorage / sessionStorage origins
- Open tab URLs
- Timestamp and label

Snapshots are stored as ``snapshot_<timestamp>_<label>.json`` files inside
the profile's ``snapshots/`` subdirectory.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.browser.config import SNAPSHOTS_DIR

logger = logging.getLogger(__name__)


@dataclass
class BrowserSnapshot:
    timestamp: float
    label: str
    cookies: list[dict[str, Any]] = field(default_factory=list)
    origins: list[dict[str, Any]] = field(default_factory=list)
    open_tabs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BrowserSnapshot":
        return cls(**data)


class SnapshotManager:
    """Save and restore browser state snapshots for a profile.

    Parameters
    ----------
    profile_dir:
        Root directory of the profile.
    """

    def __init__(self, profile_dir: Path) -> None:
        self._dir = profile_dir / SNAPSHOTS_DIR

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save(
        self,
        cookies: list[dict[str, Any]] | None = None,
        origins: list[dict[str, Any]] | None = None,
        open_tabs: list[str] | None = None,
        label: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Persist the current browser state to a snapshot file.

        Returns the path of the written snapshot.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        ts = time.time()
        snap = BrowserSnapshot(
            timestamp=ts,
            label=label,
            cookies=cookies or [],
            origins=origins or [],
            open_tabs=open_tabs or [],
            metadata=metadata or {},
        )
        name = f"snapshot_{int(ts)}_{label}.json" if label else f"snapshot_{int(ts)}.json"
        path = self._dir / name
        path.write_text(json.dumps(snap.to_dict(), indent=2), encoding="utf-8")
        logger.info("Browser snapshot saved → %s", path)
        return path

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(self, snapshot_path: Optional[Path] = None) -> Optional[BrowserSnapshot]:
        """Load a snapshot from *snapshot_path* (defaults to the latest).

        Returns the ``BrowserSnapshot`` or *None* if no snapshot exists.
        """
        if snapshot_path is None:
            snapshots = self.list_snapshots()
            if not snapshots:
                logger.warning("No snapshots found in %s", self._dir)
                return None
            snapshot_path = snapshots[-1]

        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snap = BrowserSnapshot.from_dict(data)
            logger.info("Browser snapshot loaded ← %s", snapshot_path)
            return snap
        except Exception as exc:
            logger.error("Failed to load snapshot %s: %s", snapshot_path, exc)
            return None

    # ------------------------------------------------------------------
    # List / delete
    # ------------------------------------------------------------------

    def list_snapshots(self) -> list[Path]:
        """Return snapshot paths sorted oldest → newest."""
        if not self._dir.exists():
            return []
        return sorted(
            (p for p in self._dir.iterdir() if p.suffix == ".json"),
            key=lambda p: p.stat().st_mtime,
        )

    def delete(self, snapshot_path: Path) -> None:
        if snapshot_path.exists():
            snapshot_path.unlink()
            logger.debug("Deleted snapshot: %s", snapshot_path)

    def clear_all(self) -> int:
        """Delete all snapshots. Returns count deleted."""
        count = 0
        for p in self.list_snapshots():
            p.unlink()
            count += 1
        return count
