"""Ownership System — tracks which agent owns which files, symbols, directories.

Only one agent may own a symbol at a time. Prevents concurrent writes
from corrupting the workspace.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class OwnershipConflict:
    resource: str
    claimed_by: str     # agent ID trying to claim
    owned_by: str       # current owner


class OwnershipBus:
    """Tracks ownership of resources across all running agents."""

    def __init__(self) -> None:
        self._files: Dict[str, str] = {}      # path → agent_id
        self._symbols: Dict[str, str] = {}    # symbol → agent_id
        self._dirs: Dict[str, str] = {}       # dir → agent_id
        self._lock = threading.Lock()

    def claim(
        self,
        agent_id: str,
        *,
        files: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
        dirs: Optional[List[str]] = None,
    ) -> List[OwnershipConflict]:
        """Attempt to claim resources. Returns list of conflicts (empty = success)."""
        conflicts: List[OwnershipConflict] = []
        with self._lock:
            for path in (files or []):
                if path in self._files and self._files[path] != agent_id:
                    conflicts.append(OwnershipConflict(
                        resource=path, claimed_by=agent_id, owned_by=self._files[path]
                    ))
            for sym in (symbols or []):
                if sym in self._symbols and self._symbols[sym] != agent_id:
                    conflicts.append(OwnershipConflict(
                        resource=sym, claimed_by=agent_id, owned_by=self._symbols[sym]
                    ))
            for d in (dirs or []):
                if d in self._dirs and self._dirs[d] != agent_id:
                    conflicts.append(OwnershipConflict(
                        resource=d, claimed_by=agent_id, owned_by=self._dirs[d]
                    ))
            if not conflicts:
                for path in (files or []):
                    self._files[path] = agent_id
                for sym in (symbols or []):
                    self._symbols[sym] = agent_id
                for d in (dirs or []):
                    self._dirs[d] = agent_id
        return conflicts

    def release(self, agent_id: str) -> None:
        """Release all resources owned by agent_id."""
        with self._lock:
            self._files = {k: v for k, v in self._files.items() if v != agent_id}
            self._symbols = {k: v for k, v in self._symbols.items() if v != agent_id}
            self._dirs = {k: v for k, v in self._dirs.items() if v != agent_id}

    def owner_of(self, resource: str) -> Optional[str]:
        with self._lock:
            return (
                self._files.get(resource)
                or self._symbols.get(resource)
                or self._dirs.get(resource)
            )

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "files": dict(self._files),
                "symbols": dict(self._symbols),
                "dirs": dict(self._dirs),
            }


# Module-level singleton
_bus: Optional[OwnershipBus] = None


def get_bus() -> OwnershipBus:
    global _bus
    if _bus is None:
        _bus = OwnershipBus()
    return _bus


def reset_bus() -> None:
    global _bus
    _bus = None
