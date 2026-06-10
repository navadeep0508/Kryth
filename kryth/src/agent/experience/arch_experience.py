"""Architecture Experience Engine — learns folder layouts and patterns by project type."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.experience.store import ExperienceRecord, ExperienceStore


@dataclass
class ArchExperience:
    project_type: str
    folder_structure: List[str]
    dependency_layout: Dict[str, List[str]]    # pkg → depends on
    database_design: str
    service_layout: List[str]
    api_design: str
    success_rate: float
    reuse_count: int


class ArchExperienceEngine:

    def __init__(self, store: ExperienceStore) -> None:
        self._store = store

    def record(
        self,
        project_type: str,
        folder_structure: List[str],
        dependency_layout: Dict[str, List[str]],
        success: bool,
        database_design: str = "",
        service_layout: Optional[List[str]] = None,
        api_design: str = "",
    ) -> None:
        rec = ExperienceRecord(
            id=None,
            project_hash=self._store._ph,
            kind="architecture",
            title=project_type,
            summary=f"{project_type}: {', '.join(folder_structure[:5])}",
            tags=[project_type],
            source_refs={},
            outcome="success" if success else "failure",
            importance=0.8,
            confidence=0.85 if success else 0.3,
            success_rate=1.0 if success else 0.0,
            reuse_count=0,
            novelty=0.4,
            recency_ts=time.time(),
            created_at=time.time(),
            composite_score=0.0,
            extra={
                "project_type": project_type,
                "folder_structure": folder_structure,
                "dependency_layout": dependency_layout,
                "database_design": database_design,
                "service_layout": service_layout or [],
                "api_design": api_design,
            },
        )
        self._store.insert(rec)

    def lookup(self, project_type: str) -> Optional[ArchExperience]:
        hits = self._store.search_fts(project_type, kind="architecture", limit=3)
        if not hits:
            hits = self._store.get_by_kind("architecture", limit=10, min_score=0.3)
        if not hits:
            return None
        best = hits[0]
        return ArchExperience(
            project_type=best.extra.get("project_type", project_type),
            folder_structure=best.extra.get("folder_structure", []),
            dependency_layout=best.extra.get("dependency_layout", {}),
            database_design=best.extra.get("database_design", ""),
            service_layout=best.extra.get("service_layout", []),
            api_design=best.extra.get("api_design", ""),
            success_rate=best.success_rate,
            reuse_count=best.reuse_count,
        )
