"""Local skill registry — manages ~/.kryth/skills/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from agent.ecosystem.models import SkillPackage
from agent.env import home_dir


_SKILLS_DIR = home_dir() / "skills"
_INDEX_FILE  = _SKILLS_DIR / "index.json"


class LocalRegistry:
    """Manages the on-disk skill store at ~/.kryth/skills/."""

    def __init__(self) -> None:
        _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        self._index: Dict[str, dict] = self._load_index()

    # ── Index I/O ──────────────────────────────────────────────────────────

    def _load_index(self) -> Dict[str, dict]:
        if _INDEX_FILE.exists():
            try:
                with open(_INDEX_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_index(self) -> None:
        _INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(self._index, f, indent=2)

    # ── Public API ─────────────────────────────────────────────────────────

    def has(self, skill_id: str) -> bool:
        return skill_id in self._index

    def get(self, skill_id: str) -> Optional[SkillPackage]:
        if skill_id not in self._index:
            return None
        return SkillPackage.from_dict(self._index[skill_id])

    def list_all(self) -> List[SkillPackage]:
        return [SkillPackage.from_dict(d) for d in self._index.values()]

    def register(self, pkg: SkillPackage) -> None:
        self._index[pkg.id] = pkg.to_dict()
        self._save_index()

    def unregister(self, skill_id: str) -> bool:
        if skill_id not in self._index:
            return False
        del self._index[skill_id]
        self._save_index()
        return True

    def skill_dir(self, skill_id: str) -> Path:
        return _SKILLS_DIR / skill_id

    def prompt_path(self, skill_id: str) -> Optional[Path]:
        pkg = self.get(skill_id)
        if not pkg:
            return None
        p = Path(pkg.install_path) / pkg.entry
        return p if p.exists() else None

    def read_prompt(self, skill_id: str) -> Optional[str]:
        path = self.prompt_path(skill_id)
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def usage_bump(self, skill_id: str) -> None:
        if skill_id in self._index:
            self._index[skill_id]["usage_count"] = (
                self._index[skill_id].get("usage_count", 0) + 1
            )
            self._save_index()

    def most_used(self, n: int = 5) -> List[str]:
        return sorted(
            self._index.keys(),
            key=lambda k: self._index[k].get("usage_count", 0),
            reverse=True,
        )[:n]

    def skills_dir_path(self) -> Path:
        return _SKILLS_DIR


# Module-level singleton
_local: LocalRegistry | None = None


def get_local_registry() -> LocalRegistry:
    global _local
    if _local is None:
        _local = LocalRegistry()
    return _local
