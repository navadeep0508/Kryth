"""Skill loader — loads skills from .kryth/skills/ directory.

Each skill has its own directory with:
    skill.yaml     — Metadata and tool requirements
    prompt.md      — System prompt fragment
    actions.py     — (optional) Custom action handlers

The loader:
1. Scans .kryth/skills/ for subdirectories containing skill.yaml
2. Parses the YAML config to understand tool requirements
3. Loads the prompt text for system prompt injection
4. Optionally imports custom action modules

Supports both the new structured skill format (yaml + md + py) and the
legacy flat .md skills format for backward compatibility.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _yaml_available() -> bool:
    """Check if PyYAML is installed."""
    try:
        import yaml  # noqa: F401
        return True
    except ImportError:
        return False


def _parse_yaml(text: str) -> dict | None:
    """Parse YAML text, falling back to json if yaml not available."""
    if _yaml_available():
        import yaml as _yaml
        try:
            return _yaml.safe_load(text)
        except Exception:
            pass

    # Fallback: try JSON
    import json
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


@dataclass
class SkillConfig:
    """Configuration parsed from skill.yaml."""

    name: str = ""
    version: str = "1.0"
    description: str = ""
    category: str = "general"
    requires_tools: list[str] = field(default_factory=list)
    requires_packages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    auto_select_keywords: list[str] = field(default_factory=list)
    priority: int = 100

    @classmethod
    def from_dict(cls, d: dict) -> "SkillConfig":
        return cls(
            name=d.get("name", ""),
            version=d.get("version", "1.0"),
            description=d.get("description", ""),
            category=d.get("category", "general"),
            requires_tools=d.get("requires_tools", []),
            requires_packages=d.get("requires_packages", []),
            tags=d.get("tags", []),
            auto_select_keywords=d.get("auto_select_keywords", []),
            priority=d.get("priority", 100),
        )


@dataclass
class SkillBundle:
    """A loaded skill with its config, prompt, and optional actions module."""

    config: SkillConfig
    prompt: str = ""
    actions_module: Any = None
    base_path: str = ""

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def has_actions(self) -> bool:
        return self.actions_module is not None


class SkillLoader:
    """Loads skills from the .kryth/skills/ directory tree.

    Scans for directories containing skill.yaml and loads their
    prompts and optional Python action modules.

    Usage:
        loader = SkillLoader()
        names = loader.list_skills()
        bundle = loader.load_skill("react")
        prompt = bundle.prompt
    """

    def __init__(self, skills_dir: str | None = None) -> None:
        self._skills_dir = Path(
            skills_dir or os.path.join(".kryth", "skills")
        ).absolute()
        self._cache: dict[str, SkillBundle] = {}

    def list_skills(self) -> list[str]:
        """List all available skill names."""
        names: list[str] = []

        # New format: subdirectory with skill.yaml
        if self._skills_dir.is_dir():
            for entry in self._skills_dir.iterdir():
                if entry.is_dir() and (entry / "skill.yaml").exists():
                    names.append(entry.name)

        # Legacy format: flat .md files in skills dir
        if self._skills_dir.is_dir():
            for f in self._skills_dir.iterdir():
                if f.suffix == ".md":
                    names.append(f.stem)

        return sorted(set(names))

    def load_skill(self, name: str) -> SkillBundle | None:
        """Load a skill by name.

        Returns None if the skill is not found.
        """
        if name in self._cache:
            return self._cache[name]

        # Try new format: <skills_dir>/<name>/skill.yaml + prompt.md
        skill_dir = self._skills_dir / name
        if skill_dir.is_dir() and (skill_dir / "skill.yaml").exists():
            bundle = self._load_structured_skill(name, skill_dir)
            if bundle:
                self._cache[name] = bundle
                return bundle

        # Legacy format: <skills_dir>/<name>.md
        md_path = self._skills_dir / f"{name}.md"
        if md_path.exists():
            bundle = self._load_legacy_skill(name, md_path)
            if bundle:
                self._cache[name] = bundle
                return bundle

        return None

    def get_prompt(self, name: str) -> str:
        """Get the prompt text for a skill.

        Returns empty string if the skill is not found.
        """
        bundle = self.load_skill(name)
        return bundle.prompt if bundle else ""

    def load_all(self) -> dict[str, SkillBundle]:
        """Load all available skills.

        Returns a dict of name -> SkillBundle.
        """
        bundles: dict[str, SkillBundle] = {}
        for name in self.list_skills():
            bundle = self.load_skill(name)
            if bundle:
                bundles[name] = bundle
        return bundles

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Internal loading methods
    # ------------------------------------------------------------------

    def _load_structured_skill(
        self,
        name: str,
        skill_dir: Path,
    ) -> SkillBundle | None:
        """Load a structured skill (skill.yaml + prompt.md + optional actions.py)."""
        try:
            # Parse config from skill.yaml
            config_path = skill_dir / "skill.yaml"
            if not config_path.exists():
                return None

            config_text = config_path.read_text(encoding="utf-8")
            config_data = _parse_yaml(config_text)
            if not isinstance(config_data, dict):
                logger.warning("Invalid skill.yaml for '%s'", name)
                return None
            config = SkillConfig.from_dict(config_data)

            # Override name from config if different from directory name
            if not config.name:
                config.name = name

            # Load prompt from prompt.md
            prompt_path = skill_dir / "prompt.md"
            prompt = ""
            if prompt_path.exists():
                prompt = prompt_path.read_text(encoding="utf-8")

            # Optionally load actions module
            actions_module = None
            actions_path = skill_dir / "actions.py"
            if actions_path.exists():
                actions_module = self._load_actions_module(name, actions_path)

            return SkillBundle(
                config=config,
                prompt=prompt,
                actions_module=actions_module,
                base_path=str(skill_dir),
            )
        except Exception as e:
            logger.warning("Failed to load skill '%s': %s", name, e)
            return None

    def _load_legacy_skill(
        self,
        name: str,
        md_path: Path,
    ) -> SkillBundle | None:
        """Load a legacy flat .md skill file."""
        try:
            prompt = md_path.read_text(encoding="utf-8")
            return SkillBundle(
                config=SkillConfig(name=name, description=prompt[:100]),
                prompt=prompt,
                base_path=str(md_path),
            )
        except Exception as e:
            logger.warning("Failed to load legacy skill '%s': %s", name, e)
            return None

    def _load_actions_module(
        self,
        name: str,
        actions_path: Path,
    ) -> Any:
        """Dynamically import an actions.py module from a skill directory."""
        try:
            spec = importlib.util.spec_from_file_location(
                f"kryth_skill_{name}",
                str(actions_path),
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                logger.info("Loaded actions module for skill '%s'", name)
                return module
        except Exception as e:
            logger.warning("Failed to load actions for skill '%s': %s", name, e)
        return None
