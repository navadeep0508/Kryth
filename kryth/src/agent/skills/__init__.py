"""Skill system — backward-compatible module + v2 dynamic loader.

Exports the full original API (list_skills, parse_slash, auto_select_skills,
compose_skills, BUILT_IN_SKILLS, etc.) so existing callers keep working, plus
the v2 SkillLoader for directory-based skills.
"""

from __future__ import annotations

import os
import re

from agent.settings import load_settings
from agent.skill_library import SKILLS as _STACK_SKILLS
from agent.skills.loader import SkillLoader, SkillConfig, SkillBundle


# ---------------------------------------------------------------------------
# Built-in skills (legacy + stack)
# ---------------------------------------------------------------------------

LEGACY_SKILLS = {
    "init": (
        "Read the project structure carefully. Identify language(s), build "
        "and test commands, entry points, and key conventions. Write "
        "AI_CODER.md at the project root with a concise summary that future "
        "sessions can load as context."
    ),
    "review": (
        "Review recent uncommitted changes (use `git diff` via run_command) "
        "for correctness bugs, security issues, and obvious code smells. "
        "Be terse; group findings by severity."
    ),
    "test": (
        "Run the project's test suite via run_command. If failures occur, "
        "investigate the cause and propose minimal fixes. Do not make code "
        "changes without surfacing the plan first."
    ),
    "plan": (
        "Enter plan mode. Explore the codebase read-only, design an "
        "implementation approach for the user's request, then call "
        "exit_plan_mode with the plan. Do not edit, write, or run commands."
    ),
}

BUILT_IN_SKILLS: dict[str, str] = {**LEGACY_SKILLS, **_STACK_SKILLS}

_BUILD_VERBS = re.compile(
    r"\b("
    r"build|create|make|generate|scaffold|set\s*up|bootstrap|"
    r"implement|develop|design|write|code|ship|deliver|"
    r"start|new|begin|init|initialize"
    r")\b",
    re.I,
)

AUTO_SKILL_RULES: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\blanding[-\s]?page\b", re.I), ["landing-page", "modern-ui"]),
    (re.compile(r"\bdashboard\b", re.I), ["dashboard", "modern-ui"]),
    (re.compile(r"\b(saas|multi-tenant|subscription[-\s]?app)\b", re.I), ["saas", "modern-ui"]),
    (re.compile(r"\b(rest[-\s]?api|api\s+backend|backend\s+api|http\s+api|web\s+api|json\s+api)\b", re.I), ["api"]),
    (re.compile(r"\b(api|endpoint|endpoints)\b", re.I), ["api"]),
    (re.compile(r"\b(react(\.js)?|jsx|tsx)\b", re.I), ["react", "vite"]),
    (re.compile(r"\b(next(\.js|js)?|app\s*router)\b", re.I), ["nextjs"]),
    (re.compile(r"\b(vue(\.js)?|composition\s*api)\b", re.I), ["vue", "vite"]),
    (re.compile(r"\b(svelte(kit)?)\b", re.I), ["svelte"]),
    (re.compile(r"\b(tailwind(\s*css)?)\b", re.I), ["tailwind"]),
    (re.compile(r"\bvite\b", re.I), ["vite"]),
    (re.compile(r"\bfastapi\b", re.I), ["fastapi", "api"]),
    (re.compile(r"\bflask\b", re.I), ["flask"]),
    (re.compile(r"\bdjango\b", re.I), ["django"]),
    (re.compile(r"\b(express(\.js)?|expressjs)\b", re.I), ["express", "api"]),
    (re.compile(r"\b(nest(\.js|js)?|nestjs)\b", re.I), ["nestjs", "api"]),
    (re.compile(r"\bpython\s+cli\b|\bcli\s+(in|with)\s+python\b", re.I), ["python-cli"]),
    (re.compile(r"\bnode\s+cli\b|\bcli\s+(in|with)\s+(node|typescript|ts)\b", re.I), ["node-cli"]),
    (re.compile(r"\b(cli|command[-\s]?line)\b", re.I), ["python-cli"]),
    (re.compile(r"\belectron\b", re.I), ["electron"]),
]

_SKILL_CONFLICTS = [
    ("node-cli", "python-cli"),
    ("nextjs", "react"),
    ("nestjs", "express"),
]

_SKILL_PRIORITY = {
    "system": 0,
    "modern-ui": 1,
    "landing-page": 2, "dashboard": 2, "saas": 2, "api": 2,
    "react": 3, "nextjs": 3, "vue": 3, "svelte": 3, "vite": 4, "tailwind": 4,
    "fastapi": 3, "flask": 3, "django": 3, "express": 3, "nestjs": 3,
    "python-cli": 3, "node-cli": 3, "electron": 3,
}

_ESSENTIAL_SKILLS = {"system"}
COMPOSED_BUDGET_CHARS = 4000
_MIN_PER_SKILL_CHARS = 400


# ---------------------------------------------------------------------------
# Public API (backward-compatible)
# ---------------------------------------------------------------------------

def _read_skill_file(name: str) -> str | None:
    skills_dir = load_settings().get("skills_dir", os.path.join(".kryth", "skills"))
    path = os.path.join(skills_dir, f"{name}.md")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def resolve_skill(name: str) -> str | None:
    file_skill = _read_skill_file(name)
    if file_skill:
        return file_skill
    return BUILT_IN_SKILLS.get(name)


def parse_slash(user_input: str):
    s = user_input.strip()
    if not s.startswith("/"):
        return None, None, user_input
    body = s[1:]
    parts = body.split(None, 1)
    if not parts:
        return None, None, user_input
    name = parts[0]
    args = parts[1] if len(parts) > 1 else ""
    skill = resolve_skill(name)
    if skill is None:
        return None, None, user_input
    return name, skill, args


def list_skills() -> list[str]:
    names = set(BUILT_IN_SKILLS.keys())
    skills_dir = load_settings().get("skills_dir", os.path.join(".kryth", "skills"))
    if os.path.isdir(skills_dir):
        for f in os.listdir(skills_dir):
            if f.endswith(".md"):
                names.add(f[:-3])
    return sorted(names)


def _is_build_request(user_input: str) -> bool:
    return bool(_BUILD_VERBS.search(user_input))


def auto_select_skills(user_input: str, project_context: str = "") -> list[str]:
    if not user_input or user_input.lstrip().startswith("/"):
        return []
    if not _is_build_request(user_input):
        return []

    skill_lines: list[str] = []
    for name in BUILT_IN_SKILLS:
        body = BUILT_IN_SKILLS.get(name, "")
        first_line = body.strip().split("\n")[0] if body else ""
        if first_line.startswith("[Skill:"):
            first_line = first_line.split("]", 1)[1].strip()
        skill_lines.append(f"- {name}: {first_line[:100]}")

    ctx_section = f"\n\nCurrent project context:\n{project_context[:1500]}" if project_context else ""

    system_prompt = (
        "You are a skill selector for an AI coding agent.\n"
        "Given a user request and the current project, choose which skills are "
        "genuinely needed.\n\nAvailable skills:\n"
        + "\n".join(skill_lines)
        + "\n\nRules:\n"
        "- Select ONLY skills directly relevant to THIS request in THIS project.\n"
        "- Include 'modern-ui' for any visible UI work.\n"
        "- Include 'system' when any other skill is selected.\n"
        "- Return ONLY a JSON array, e.g. [\"react\", \"tailwind\", \"system\"]\n"
        "- Return [] if no skills are relevant."
    )

    try:
        import json as _json
        from agent.llm import _get_client, PLANNER_MODEL
        client = _get_client()
        response = client.chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input + ctx_section},
            ],
            temperature=0,
            max_tokens=200,
        )
        text = (response.choices[0].message.content or "").strip()
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            selected = _json.loads(text[start:end + 1])
            if isinstance(selected, list):
                valid = [s for s in selected if s in BUILT_IN_SKILLS]
                if valid:
                    if any(s != "system" for s in valid) and "system" not in valid:
                        valid.append("system")
                    for specific, generic in _SKILL_CONFLICTS:
                        if specific in valid and generic in valid:
                            valid.remove(generic)
                    return sorted(valid, key=lambda n: (_SKILL_PRIORITY.get(n, 9), n))
                return []
    except Exception:
        pass

    selected_kw: set[str] = set()
    for pattern, skills in AUTO_SKILL_RULES:
        if pattern.search(user_input):
            selected_kw.update(skills)
    if not selected_kw:
        return []
    selected_kw.add("system")
    for specific, generic in _SKILL_CONFLICTS:
        if specific in selected_kw and generic in selected_kw:
            selected_kw.discard(generic)
    return sorted(selected_kw, key=lambda n: (_SKILL_PRIORITY.get(n, 9), n))


def compose_skills(names: list[str]) -> str:
    if not names:
        return ""
    essential = [n for n in names if n in _ESSENTIAL_SKILLS]
    others = [n for n in names if n not in _ESSENTIAL_SKILLS]
    parts: list[str] = []
    used = 0
    for n in essential:
        body = resolve_skill(n)
        if not body:
            continue
        chunk = f"[Skill: {n}]\n{body}"
        parts.append(chunk)
        used += len(chunk)
    if others:
        remaining = max(0, COMPOSED_BUDGET_CHARS - used)
        per_skill = max(_MIN_PER_SKILL_CHARS, remaining // len(others))
        for n in others:
            body = resolve_skill(n)
            if not body:
                continue
            chunk = f"[Skill: {n}]\n{body}"
            if len(chunk) > per_skill:
                cut = chunk.rfind("\n\n", 0, per_skill)
                if cut < per_skill // 2:
                    cut = per_skill
                chunk = chunk[:cut].rstrip() + f"\n\n[...{n} skill trimmed]"
            parts.append(chunk)
    return "\n\n".join(parts)


__all__ = [
    # v2
    "SkillLoader", "SkillConfig", "SkillBundle",
    # legacy (backward-compatible)
    "BUILT_IN_SKILLS", "LEGACY_SKILLS", "AUTO_SKILL_RULES",
    "list_skills", "parse_slash", "resolve_skill",
    "auto_select_skills", "compose_skills", "_is_build_request",
    "COMPOSED_BUDGET_CHARS",
]
