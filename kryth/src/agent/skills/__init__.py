"""Skill system — dynamic skill loading from .kryth/skills/ folder.

Each skill lives in its own subdirectory under `.kryth/skills/<name>/` and
contains:
    - ``skill.yaml`` — config describing what tools the skill needs
    - ``prompt.md`` — system prompt fragment injected when skill is active
    - ``actions.py`` — (optional) Python module with custom action handlers

The skill loader reads yaml config to know which tools a skill needs and
loads the prompt text for injection into the system message.

Usage:
    from agent.skills.loader import SkillLoader

    loader = SkillLoader()
    skills = loader.list_skills()
    prompt = loader.get_prompt("react")
"""

from agent.skills.loader import SkillLoader, SkillConfig, SkillBundle

__all__ = [
    "SkillLoader",
    "SkillConfig",
    "SkillBundle",
]
