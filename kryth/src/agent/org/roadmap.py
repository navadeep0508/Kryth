"""Roadmap & quarter planning (Phase C).

Models the work hierarchy: Portfolio → Program → Epic → Mission → Task. Supports
priority/quarter/capacity planning and progress roll-up. Progress flows bottom-up
— a parent's progress is the weighted mean of its children — so an epic or
program shows true completion derived from its missions/tasks.

Pure tree model. Additive; does not run anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Allowed nesting (parent → child levels).
_LEVELS = ("portfolio", "program", "epic", "mission", "task")
_CHILD = {"portfolio": "program", "program": "epic", "epic": "mission", "mission": "task"}


@dataclass
class RoadmapNode:
    id: str
    level: str                      # one of _LEVELS
    title: str = ""
    parent: str = ""
    quarter: str = ""               # e.g. "2026-Q3"
    priority: int = 5
    capacity: int = 0               # planned worker/effort units
    own_progress: float = 0.0       # leaf-level progress (tasks/missions)
    children: List[str] = field(default_factory=list)


class Roadmap:
    """Hierarchical roadmap with progress roll-up and quarter planning."""

    def __init__(self) -> None:
        self._nodes: Dict[str, RoadmapNode] = {}

    def add(self, node_id: str, level: str, *, title: str = "", parent: str = "",
            quarter: str = "", priority: int = 5, capacity: int = 0) -> RoadmapNode:
        if level not in _LEVELS:
            raise ValueError(f"unknown level {level!r}; expected one of {_LEVELS}")
        if parent:
            p = self._nodes.get(parent)
            if p is None:
                raise ValueError(f"unknown parent {parent!r}")
            if _CHILD.get(p.level) != level:
                raise ValueError(f"{level} cannot nest under {p.level}")
        n = RoadmapNode(id=node_id, level=level, title=title or node_id,
                        parent=parent, quarter=quarter, priority=priority, capacity=capacity)
        self._nodes[node_id] = n
        if parent:
            self._nodes[parent].children.append(node_id)
        return n

    def set_progress(self, node_id: str, progress: float) -> None:
        n = self._nodes.get(node_id)
        if n:
            n.own_progress = max(0.0, min(100.0, progress))

    def progress(self, node_id: str) -> float:
        """Roll-up progress: leaf → own_progress; parent → mean of children."""
        n = self._nodes.get(node_id)
        if not n:
            return 0.0
        if not n.children:
            return round(n.own_progress, 1)
        vals = [self.progress(c) for c in n.children]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    def children_of(self, node_id: str) -> List[RoadmapNode]:
        n = self._nodes.get(node_id)
        return [self._nodes[c] for c in n.children] if n else []

    def by_quarter(self, quarter: str) -> List[RoadmapNode]:
        return [n for n in self._nodes.values() if n.quarter == quarter]

    def quarters(self) -> List[str]:
        return sorted({n.quarter for n in self._nodes.values() if n.quarter})

    def capacity_plan(self, quarter: str) -> dict:
        """Planned capacity for a quarter, grouped by level."""
        nodes = self.by_quarter(quarter)
        by_level: Dict[str, int] = {}
        for n in nodes:
            by_level[n.level] = by_level.get(n.level, 0) + n.capacity
        return {"quarter": quarter, "nodes": len(nodes),
                "total_capacity": sum(n.capacity for n in nodes), "by_level": by_level}

    def tree(self, root: Optional[str] = None) -> List[dict]:
        """Flatten to indent-annotated rows for rendering."""
        rows: List[dict] = []
        roots = ([self._nodes[root]] if root and root in self._nodes
                 else [n for n in self._nodes.values() if not n.parent])

        def walk(n: RoadmapNode, depth: int):
            rows.append({"id": n.id, "level": n.level, "title": n.title,
                         "depth": depth, "quarter": n.quarter,
                         "progress": self.progress(n.id), "priority": n.priority})
            for c in n.children:
                walk(self._nodes[c], depth + 1)

        for r in sorted(roots, key=lambda n: (n.priority, n.id)):
            walk(r, 0)
        return rows
