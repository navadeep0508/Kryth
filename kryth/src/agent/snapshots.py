"""Per-file checkpoint storage for KRYTH.

Before any tool mutates a file (write_file, edit_file, multi_edit,
delete_file), ``snapshot(path)`` captures the prior bytes under
``~/.kryth/snapshots/<project_hash>/<safe_path>/<unix_ts>.bak``.

The store keeps the ``MAX_SNAPSHOTS_PER_FILE`` newest backups per file;
older ones are pruned on the next snapshot. Restoring is exposed via
``restore(path, index)`` and the ``rollback_file`` / ``list_snapshots``
tools so the agent (or the user via ``/undo``) can roll back a regrettable
edit without leaving the REPL.

The module is intentionally side-channel: failures (full disk, perms)
never raise into the mutation path — they are logged-and-swallowed. A
missing snapshot is far less bad than refusing to write a file because
the snapshot directory is unwritable.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from agent.env import home_dir

MAX_SNAPSHOTS_PER_FILE = 20

# Snapshot root. Mirrors persistence and honours env overrides.
def _root() -> Path:
    return home_dir() / "snapshots"

def _project_hash() -> str:
    """Short stable identifier for the current working directory."""
    import hashlib
    cwd = os.getcwd()
    return hashlib.sha1(cwd.encode("utf-8")).hexdigest()[:8]

_PATH_SANITISE_RE = re.compile(r"[^A-Za-z0-9._-]+")

def _safe_path_key(path: str | Path) -> str:
    """Map an absolute path to a single-segment, filesystem-safe key.

    We want each file's snapshots in their own dir but also want the key
    to be reversible-by-inspection (so an operator can find the right
    file). Compromise: replace any run of non-portable chars with ``_``,
    then prepend a short hash of the original path to defuse collisions
    on case-insensitive filesystems (Windows: ``Foo.py`` and ``foo.py``
    would collide).
    """
    import hashlib
    abs_path = str(Path(path).resolve())
    short_hash = hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:6]
    safe = _PATH_SANITISE_RE.sub("_", abs_path)
    return f"{short_hash}_{safe}"

def _file_snapshot_dir(path: str | Path) -> Path:
    return _root() / _project_hash() / _safe_path_key(path)

def _ensure(d: Path) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    return d

def snapshot(path: str | Path) -> Path | None:
    """Copy ``path``'s current bytes into the snapshot store.

    Returns the snapshot Path on success, ``None`` if the source file
    doesn't exist (a create operation has nothing to back up) or the
    store can't be written. Never raises.
    """
    try:
        src = Path(path)
        if not src.exists():
            return None
        snap_dir = _ensure(_file_snapshot_dir(path))
        ts = int(time.time() * 1000)
        dest = snap_dir / f"{ts}.bak"
        shutil.copy2(src, dest)
        _prune(snap_dir)
        return dest
    except Exception:
        return None

def _prune(snap_dir: Path) -> None:
    """Keep only the newest MAX_SNAPSHOTS_PER_FILE backups."""
    try:
        backups = sorted(snap_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[MAX_SNAPSHOTS_PER_FILE:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass

def list_snapshots(path: str | Path) -> list[dict]:
    """Return a list of snapshot records for ``path``, newest first.

    Each record has:
        ``backup_path``  — absolute path to the ``.bak`` file
        ``ts``           — unix timestamp (float seconds)
        ``size``         — bytes
    """
    try:
        snap_dir = _file_snapshot_dir(path)
        if not snap_dir.exists():
            return []
        backups = sorted(snap_dir.glob("*.bak"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for b in backups:
            try:
                stat = b.stat()
                result.append({
                    "backup_path": str(b),
                    "ts": stat.st_mtime,
                    "size": stat.st_size,
                })
            except Exception:
                pass
        return result
    except Exception:
        return []

def restore(path: str | Path, index: int = 0) -> tuple[bool, str]:
    """Restore ``path`` from the snapshot at position ``index`` (0 = newest).

    Returns ``(True, message)`` on success, ``(False, error)`` on failure.
    """
    try:
        items = list_snapshots(path)
        if not items:
            return False, f"no snapshots available for {path}"
        if index < 0 or index >= len(items):
            return False, f"snapshot index {index} out of range (0..{len(items)-1})"
        backup_path = items[index]["backup_path"]
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, dest)
        return True, f"restored {path} from snapshot {index} ({backup_path})"
    except Exception as e:
        return False, f"restore failed: {e}"

__all__ = ["snapshot", "list_snapshots", "restore", "MAX_SNAPSHOTS_PER_FILE"]
