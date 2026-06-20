"""Leadership hierarchy (Phase F).

A three-tier org chart — Department Head → Team Lead → Worker — built on top of
the departments/teams the orchestrator already forms. Tracks per-node
productivity (tasks completed) and utilization so leadership effectiveness is
visible. Pure tree model; it labels and aggregates, it does not assign work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

_ROLES = ("head", "lead", "worker")
_CHILD_ROLE = {"head": "lead", "lead": "worker"}


@dataclass
class OrgMember:
    id: str
    role: str                       # head | lead | worker
    name: str = ""
    parent: str = ""
    department: str = ""
    tasks_done: int = 0
    tasks_failed: int = 0
    busy: bool = False
    children: List[str] = field(default_factory=list)

    @property
    def productivity(self) -> float:
        total = self.tasks_done + self.tasks_failed
        return round(self.tasks_done / total, 3) if total else 0.0


class Hierarchy:
    """Department-head → lead → worker chart with productivity roll-up."""

    def __init__(self) -> None:
        self._members: Dict[str, OrgMember] = {}

    def add(self, member_id: str, role: str, *, name: str = "", parent: str = "",
            department: str = "") -> OrgMember:
        if role not in _ROLES:
            raise ValueError(f"unknown role {role!r}; expected {_ROLES}")
        if parent:
            p = self._members.get(parent)
            if p is None:
                raise ValueError(f"unknown parent {parent!r}")
            if _CHILD_ROLE.get(p.role) != role:
                raise ValueError(f"{role} cannot report to {p.role}")
            department = department or p.department
        m = OrgMember(id=member_id, role=role, name=name or member_id,
                      parent=parent, department=department)
        self._members[member_id] = m
        if parent:
            self._members[parent].children.append(member_id)
        return m

    def record(self, member_id: str, *, done: int = 0, failed: int = 0,
               busy: Optional[bool] = None) -> None:
        m = self._members.get(member_id)
        if not m:
            return
        m.tasks_done += done
        m.tasks_failed += failed
        if busy is not None:
            m.busy = busy

    def team_productivity(self, lead_id: str) -> float:
        """A lead's team productivity = mean worker productivity."""
        lead = self._members.get(lead_id)
        if not lead:
            return 0.0
        workers = [self._members[c] for c in lead.children]
        vals = [w.productivity for w in workers if (w.tasks_done + w.tasks_failed)]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    def team_utilization(self, lead_id: str) -> float:
        lead = self._members.get(lead_id)
        if not lead or not lead.children:
            return 0.0
        busy = sum(1 for c in lead.children if self._members[c].busy)
        return round(busy / len(lead.children), 3)

    def leadership_effectiveness(self, head_id: str) -> float:
        """A head's effectiveness = mean of its leads' team productivity."""
        head = self._members.get(head_id)
        if not head:
            return 0.0
        vals = [self.team_productivity(c) for c in head.children]
        vals = [v for v in vals if v > 0]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    def chart(self) -> List[dict]:
        rows: List[dict] = []
        roots = [m for m in self._members.values() if not m.parent]

        def walk(m: OrgMember, depth: int):
            rows.append({"id": m.id, "role": m.role, "name": m.name, "depth": depth,
                         "department": m.department, "productivity": m.productivity,
                         "busy": m.busy})
            for c in m.children:
                walk(self._members[c], depth + 1)

        for r in sorted(roots, key=lambda m: m.id):
            walk(r, 0)
        return rows
