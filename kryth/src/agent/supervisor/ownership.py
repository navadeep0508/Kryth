"""Ownership Enforcer — prevent resource conflicts across parallel agents.

Tracks and enforces ownership of:
  - Files (path)
  - Symbols (qualified name)
  - Directories
  - Ports
  - Browsers / browser profiles
  - Terminal worker slots

When an agent claims a resource, no other agent can write to it until
the owner releases. Read-only access is always allowed.

Conflict resolution:
  1. Raise a ConflictError immediately (fail fast)
  2. Callers can ask can_claim() before claiming to avoid surprises
  3. Supervisor uses delay_until_released() to serialize conflicting tasks
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Set


class ConflictError(Exception):
    """Raised when a resource is already owned by another agent."""
    def __init__(self, resource: str, owner: str, requester: str):
        super().__init__(
            f"Resource '{resource}' is owned by '{owner}'; '{requester}' cannot claim it"
        )
        self.resource = resource
        self.owner = owner
        self.requester = requester


@dataclass
class ResourceLock:
    resource: str
    owner: str
    kind: str         # file | symbol | directory | port | browser | terminal
    claimed_at: float = field(default_factory=time.monotonic)


class OwnershipEnforcer:
    """Thread-safe resource ownership registry for parallel agents."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owned: Dict[str, ResourceLock] = {}   # resource_key → lock

    # ------------------------------------------------------------------
    # Claim / release

    def claim(
        self,
        agent_id: str,
        resource: str,
        kind: str = "file",
        *,
        force: bool = False,
    ) -> None:
        """Claim a resource for agent_id. Raises ConflictError if taken.

        force=True allows takeover (use only for crash recovery).
        """
        key = self._key(kind, resource)
        with self._lock:
            existing = self._owned.get(key)
            if existing and existing.owner != agent_id and not force:
                raise ConflictError(resource, existing.owner, agent_id)
            self._owned[key] = ResourceLock(
                resource=resource, owner=agent_id, kind=kind
            )

    def release(self, agent_id: str, resource: str, kind: str = "file") -> None:
        key = self._key(kind, resource)
        with self._lock:
            existing = self._owned.get(key)
            if existing and existing.owner == agent_id:
                del self._owned[key]

    def release_all(self, agent_id: str) -> List[str]:
        """Release every resource owned by agent_id. Returns released keys."""
        with self._lock:
            to_release = [k for k, v in self._owned.items() if v.owner == agent_id]
            for k in to_release:
                del self._owned[k]
        return to_release

    # ------------------------------------------------------------------
    # Query

    def can_claim(
        self, agent_id: str, resource: str, kind: str = "file"
    ) -> bool:
        key = self._key(kind, resource)
        with self._lock:
            existing = self._owned.get(key)
            return existing is None or existing.owner == agent_id

    def owner_of(self, resource: str, kind: str = "file") -> Optional[str]:
        key = self._key(kind, resource)
        with self._lock:
            lock = self._owned.get(key)
            return lock.owner if lock else None

    def conflicts_for(self, agent_id: str, resources: list) -> list:
        """Return list of (resource, current_owner) tuples that conflict."""
        conflicts = []
        for item in resources:
            if isinstance(item, tuple):
                kind, res = item
            else:
                kind, res = "file", item
            owner = self.owner_of(res, kind)
            if owner and owner != agent_id:
                conflicts.append((res, owner))
        return conflicts

    def owned_by(self, agent_id: str) -> list:
        """Return all resources owned by this agent."""
        with self._lock:
            return [v for v in self._owned.values() if v.owner == agent_id]

    # ------------------------------------------------------------------
    # Blocking wait

    def wait_for_release(
        self, resource: str, kind: str = "file", timeout: float = 30.0
    ) -> bool:
        """Block until resource is released or timeout expires.

        Returns True if resource became free, False if timed out.
        """
        key = self._key(kind, resource)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if key not in self._owned:
                    return True
            time.sleep(0.1)
        return False

    # ------------------------------------------------------------------
    # Status

    def snapshot(self) -> dict:
        with self._lock:
            return {
                k: {"owner": v.owner, "kind": v.kind,
                    "age_s": time.monotonic() - v.claimed_at}
                for k, v in self._owned.items()
            }

    def format_table(self) -> str:
        snap = self.snapshot()
        if not snap:
            return "(no ownership locks held)"
        lines = ["Resource                 Kind      Owner          Age"]
        for key, info in sorted(snap.items()):
            short_key = key[key.find(":")+1:][:25]
            lines.append(
                f"{short_key:<25}{info['kind']:<10}"
                f"{info['owner']:<15}{info['age_s']:.0f}s"
            )
        return "\n".join(lines)

    @staticmethod
    def _key(kind: str, resource: str) -> str:
        return f"{kind}:{resource}"


from typing import List  # noqa — needed at runtime for release_all type hint

# Module-level singleton
ownership_enforcer = OwnershipEnforcer()
