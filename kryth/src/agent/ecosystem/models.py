"""Skill package metadata model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class SkillPackage:
    id: str
    name: str
    version: str
    description: str
    author: str = "community"
    tags: List[str] = field(default_factory=list)
    entry: str = "prompt.md"          # prompt.md (prompt skill) or main.py (code skill)
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    download_url: str = ""
    install_path: str = ""            # set after install
    verified: bool = False
    downloads: int = 0
    rating: float = 0.0

    # chains: list of skill IDs this skill depends on (for DAG execution)
    chains: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "SkillPackage":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            version=d.get("version", "1.0.0"),
            description=d.get("description", ""),
            author=d.get("author", "community"),
            tags=d.get("tags", []),
            entry=d.get("entry", "prompt.md"),
            dependencies=d.get("dependencies", []),
            permissions=d.get("permissions", []),
            download_url=d.get("download_url", ""),
            install_path=d.get("install_path", ""),
            verified=d.get("verified", False),
            downloads=d.get("downloads", 0),
            rating=d.get("rating", 0.0),
            chains=d.get("chains", []),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "tags": self.tags,
            "entry": self.entry,
            "dependencies": self.dependencies,
            "permissions": self.permissions,
            "download_url": self.download_url,
            "install_path": self.install_path,
            "verified": self.verified,
            "downloads": self.downloads,
            "rating": self.rating,
            "chains": self.chains,
        }

    @classmethod
    def from_file(cls, path: Path) -> "SkillPackage":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
