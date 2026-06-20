"""Knowledge capitalization (Phase J).

Converts completed missions into reusable organizational assets — patterns,
templates, architectures, best practices, playbooks, reusable components — and
tracks reuse rate, knowledge growth, and impact. Pure store; capitalization is
a deterministic extraction from a mission record you pass in.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class AssetKind(Enum):
    PATTERN = "pattern"
    TEMPLATE = "template"
    ARCHITECTURE = "architecture"
    BEST_PRACTICE = "best_practice"
    PLAYBOOK = "playbook"
    COMPONENT = "component"


@dataclass
class KnowledgeAsset:
    id: str
    kind: AssetKind
    name: str
    source_mission: str = ""
    tags: List[str] = field(default_factory=list)
    reuse_count: int = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind.value, "name": self.name,
                "source_mission": self.source_mission, "tags": list(self.tags),
                "reuse_count": self.reuse_count}


class KnowledgeBase:
    """Reusable-asset registry with reuse/growth/impact metrics."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._assets: Dict[str, KnowledgeAsset] = {}
        self._missions_capitalized = 0

    def register(self, asset_id: str, kind: AssetKind, name: str, *,
                 source_mission: str = "", tags=None) -> KnowledgeAsset:
        with self._lock:
            a = KnowledgeAsset(id=asset_id, kind=kind, name=name,
                               source_mission=source_mission, tags=list(tags or []))
            self._assets[asset_id] = a
            return a

    def capitalize(self, mission: dict) -> List[KnowledgeAsset]:
        """Extract reusable assets from a completed mission record.

        ``mission`` = {id, category, components/sections, files, ...}. We mine the
        obvious reusable shapes: each component → a component asset; the overall
        category → a pattern; a multi-section build → a template; tests → a
        best-practice. Deterministic and conservative."""
        with self._lock:
            self._missions_capitalized += 1
        mid = mission.get("id", "mission")
        created: List[KnowledgeAsset] = []

        for comp in mission.get("components", []) or []:
            created.append(self.register(f"{mid}:comp:{comp}", AssetKind.COMPONENT,
                                         f"{comp} component", source_mission=mid, tags=[comp]))
        if mission.get("sections"):
            created.append(self.register(f"{mid}:template", AssetKind.TEMPLATE,
                                         f"{mission.get('category', 'page')} template",
                                         source_mission=mid, tags=list(mission["sections"])))
        cat = mission.get("category")
        if cat:
            created.append(self.register(f"{mid}:pattern:{cat}", AssetKind.PATTERN,
                                         f"{cat} pattern", source_mission=mid, tags=[cat]))
        if mission.get("has_tests"):
            created.append(self.register(f"{mid}:bp:testing", AssetKind.BEST_PRACTICE,
                                         "test-first workflow", source_mission=mid))
        return created

    def find(self, *, kind: Optional[AssetKind] = None, tag: str = "") -> List[KnowledgeAsset]:
        with self._lock:
            out = list(self._assets.values())
        if kind is not None:
            out = [a for a in out if a.kind is kind]
        if tag:
            out = [a for a in out if tag in a.tags]
        return out

    def reuse(self, asset_id: str) -> bool:
        with self._lock:
            a = self._assets.get(asset_id)
            if not a:
                return False
            a.reuse_count += 1
            return True

    def metrics(self) -> dict:
        with self._lock:
            assets = list(self._assets.values())
            reused = sum(1 for a in assets if a.reuse_count > 0)
            total_reuse = sum(a.reuse_count for a in assets)
            return {
                "total_assets": len(assets),
                "missions_capitalized": self._missions_capitalized,
                "reuse_rate": round(reused / len(assets), 3) if assets else 0.0,
                "total_reuses": total_reuse,
                "knowledge_growth": len(assets),   # assets accumulated
                "impact": total_reuse,             # reuses = value delivered
                "by_kind": {k.value: sum(1 for a in assets if a.kind is k) for k in AssetKind},
            }

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [a.to_dict() for a in self._assets.values()]
