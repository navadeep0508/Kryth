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


MAX_SNAPSHOTS_PER_FILE = 10


def _root() -> Path:
    """Snapshot root. Mirrors persistence and honours env overrides."""
    return home_dir() / "snapshots"


def _project_hash(cwd: str | Path = ".") -> str:
    # Lazy import: persistence pulls in dataclasses/json that we don't
    # need until first snapshot.
    from agent.persistence import project_hash_for
    return project_hash_for(cwd)


_PATH_SANITISE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_path_key(abs_path: str) -> str:
    """Map an absolute path to a single-segment, filesystem-safe key.

    We want each file's snapshots in their own dir but also want the key
    to be reversible-by-inspection (so an operator can find the right
    file). Compromise: replace any run of non-portable chars with ``_``,
    then prepend a short hash of the original path to defuse collisions
    on case-insensitive filesystems (Windows: ``Foo.py`` and ``foo.py``
    would collide).
    """
    import hashlib
    h = hashlib.sha1(abs_path.encode("utf-8", "replace")).hexdigest()[:8]
    cleaned = _PATH_SANITISE_RE.sub("_", abs_path).strip("_")[:120]
    return f"{h}__{cleaned}"


def _file_snapshot_dir(path: str) -> Path:
    abs_path = str(Path(path).resolve())
    return _root() / _project_hash() / _safe_path_key(abs_path)


def _ensure(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def snapshot(path: str) -> Path | None:
    """Copy ``path``'s current bytes into the snapshot store.

    Returns the snapshot Path on success, ``None`` if the source file
    doesn't exist (a create operation has nothing to back up) or the
    store can't be written. Never raises.
    """
    src = Path(path)
    if not src.is_file():
        return None

    try:
        snap_dir = _file_snapshot_dir(path)
        _ensure(snap_dir)
        ts = f"{int(time.time() * 1000)}.bak"
        dst = snap_dir / ts
        shutil.copy2(src, dst)
    except OSError:
        return None

    _prune(snap_dir)
    return dst


def _prune(snap_dir: Path) -> None:
    try:
        files = sorted(
            (p for p in snap_dir.iterdir() if p.suffix == ".bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for old in files[MAX_SNAPSHOTS_PER_FILE:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_snapshots(path: str) -> list[dict]:
    """Newest-first listing of available snapshots for ``path``.

    Each entry: ``{"index": int, "ts": float, "size": int, "backup_path": str}``.
    ``index`` is stable for the call (0 = newest); pass it to ``restore``.
    """
    snap_dir = _file_snapshot_dir(path)
    if not snap_dir.is_dir():
        return []
    try:
        entries = sorted(
            (p for p in snap_dir.iterdir() if p.suffix == ".bak"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    out: list[dict] = []
    for i, p in enumerate(entries):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append({
            "index": i,
            "ts": st.st_mtime,
            "size": st.st_size,
            "backup_path": str(p),
        })
    return out


def restore(path: str, index: int = 0) -> tuple[bool, str]:
    """Replace ``path``'s contents with snapshot ``index`` (0 = newest).

    Returns ``(success, message)``. Snapshots the current contents first
    so the rollback itself is undoable — the agent (or operator) can
    re-restore the newer version if the rollback was a mistake.
    """
    backups = list_snapshots(path)
    if not backups:
        return False, f"no snapshots available for {path}"
    if index < 0 or index >= len(backups):
        return False, (
            f"snapshot index {index} out of range (have {len(backups)} backups)"
        )

    src = Path(backups[index]["backup_path"])
    if not src.is_file():
        return False, f"snapshot file vanished: {src}"

    snapshot(path)

    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
    except OSError as e:
        return False, f"could not restore {path}: {e}"

    try:
        from agent.tools._file_ops import _invalidate_indexes
        _invalidate_indexes(path)
    except Exception:
        pass

    return True, (
        f"restored {path} from snapshot {index} "
        f"(taken {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(backups[index]['ts']))})"
    )


__all__ = [
    "MAX_SNAPSHOTS_PER_FILE",
    "snapshot",
    "list_snapshots",
    "restore",
]
