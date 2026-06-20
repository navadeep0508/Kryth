"""Autonomous departments (Phase E).

Persistent organizational units (Frontend, Backend, Platform, Testing, Docs,
DevOps) that outlive any single mission. Each department accumulates reputation
(success/failure across missions), tracks capacity vs current workload, and
reports utilization. This is a model layer — departments don't execute; they map
to the agent teams the orchestrator already creates.

Persistence is JSON-backed (reputation must survive missions, per the spec).
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

# Default departments the spec calls out.
DEFAULT_DEPARTMENTS = ("frontend", "backend", "platform", "testing", "documentation", "devops")


def departments_path() -> Path:
    p = os.environ.get("KRYTH_DEPARTMENTS_PATH")
    if p:
        return Path(p)
    return Path(os.path.expanduser("~")) / ".kryth" / "departments.json"


@dataclass
class Department:
    name: str
    capacity: int = 4                  # max concurrent workers it can field
    workload: int = 0                  # currently assigned work units
    owned_files: List[str] = field(default_factory=list)
    missions_succeeded: int = 0
    missions_failed: int = 0

    @property
    def utilization(self) -> float:
        return min(1.0, self.workload / self.capacity) if self.capacity else 0.0

    @property
    def reputation(self) -> float:
        total = self.missions_succeeded + self.missions_failed
        return round(self.missions_succeeded / total, 3) if total else 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name, "capacity": self.capacity, "workload": self.workload,
            "utilization": round(self.utilization, 3), "reputation": self.reputation,
            "owned_files": list(self.owned_files),
            "missions_succeeded": self.missions_succeeded,
            "missions_failed": self.missions_failed,
        }


class DepartmentRegistry:
    """Persistent registry of departments + their reputation/utilization."""

    def __init__(self, path: Optional[Path] = None, *, autoload: bool = True,
                 seed_defaults: bool = True):
        self.path = Path(path) if path else departments_path()
        self._lock = threading.RLock()
        self._depts: Dict[str, Department] = {}
        if seed_defaults:
            for d in DEFAULT_DEPARTMENTS:
                self._depts[d] = Department(name=d)
        if autoload:
            self.load()

    def ensure(self, name: str, *, capacity: int = 4) -> Department:
        with self._lock:
            d = self._depts.get(name)
            if d is None:
                d = Department(name=name, capacity=capacity)
                self._depts[name] = d
            return d

    def get(self, name: str) -> Optional[Department]:
        return self._depts.get(name)

    def assign(self, name: str, units: int = 1, files: Optional[List[str]] = None) -> None:
        with self._lock:
            d = self.ensure(name)
            d.workload += units
            if files:
                for f in files:
                    if f not in d.owned_files:
                        d.owned_files.append(f)

    def release(self, name: str, units: int = 1) -> None:
        with self._lock:
            d = self._depts.get(name)
            if d:
                d.workload = max(0, d.workload - units)

    def record_mission(self, name: str, *, success: bool) -> None:
        with self._lock:
            d = self.ensure(name)
            if success:
                d.missions_succeeded += 1
            else:
                d.missions_failed += 1

    def rank_by_reputation(self) -> List[Department]:
        with self._lock:
            return sorted(self._depts.values(),
                          key=lambda d: (d.reputation, d.missions_succeeded), reverse=True)

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [d.to_dict() for d in self._depts.values()]

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        with self._lock:
            try:
                if self.path.exists():
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    for name, d in raw.items():
                        self._depts[name] = Department(
                            name=name, capacity=d.get("capacity", 4),
                            workload=d.get("workload", 0),
                            owned_files=d.get("owned_files", []),
                            missions_succeeded=d.get("missions_succeeded", 0),
                            missions_failed=d.get("missions_failed", 0))
            except Exception:
                pass

    def save(self) -> None:
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                raw = {d.name: d.to_dict() for d in self._depts.values()}
                self.path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            except Exception:
                pass
