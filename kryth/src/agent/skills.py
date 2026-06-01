"""Skills: prompt fragments injected per turn.

Two sources:

- ``BUILT_IN_SKILLS`` — defined in ``agent/skill_library.py`` plus a few
  legacy entries (init, review, test, plan).
- User skills — markdown files in ``.kryth/skills/<name>.md``. These
  override built-ins of the same name.

Auto-selection: when the user prompt isn't a ``/skill`` invocation,
``auto_select_skills`` matches keywords against ``AUTO_SKILL_RULES`` and
returns the names to inject. The agent loop composes their bodies and
passes them as ``extra_system``.
"""

from __future__ import annotations

import os
import re

from agent.settings import load_settings
from agent.skill_library import SKILLS as _STACK_SKILLS


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


# A prompt is treated as a "build request" only if it contains one of
# these verbs. Documentation questions ("how do react hooks work?")
# don't, so they don't auto-inject stack skills.
_BUILD_VERBS = re.compile(
    r"\b("
    r"build|create|make|generate|scaffold|set\s*up|bootstrap|"
    r"implement|develop|design|write|code|ship|deliver|"
    r"start|new|begin|init|initialize"
    r")\b",
    re.I,
)


# Auto-skill detection rules. Each rule is (regex, list of skill names).
# Multiple rules can match — the resulting set is composed and injected
# in priority order (system first, archetype second, stack last).
#
# The rules below assume ``_is_build_request`` already gated the prompt.
AUTO_SKILL_RULES: list[tuple[re.Pattern, list[str]]] = [
    # Archetypes — UI archetypes pull in modern-ui automatically so the
    # design polish layer (typography, palette, shadows, animations) is
    # always present alongside the structural rules.
    (re.compile(r"\blanding[-\s]?page\b", re.I), ["landing-page", "modern-ui"]),
    (re.compile(r"\bdashboard\b", re.I), ["dashboard", "modern-ui"]),
    (re.compile(r"\b(saas|multi-tenant|subscription[-\s]?app)\b", re.I), ["saas", "modern-ui"]),
    (re.compile(r"\b(rest[-\s]?api|api\s+backend|backend\s+api|http\s+api|web\s+api|json\s+api)\b", re.I), ["api"]),
    # Fallback: a bare 'api' or 'endpoint' inside a build request implies
    # API-style work. The build-verb gate has already filtered out
    # documentation prompts like "what is an api".
    (re.compile(r"\b(api|endpoint|endpoints)\b", re.I), ["api"]),

    # Stacks — frontend
    (re.compile(r"\b(react(\.js)?|jsx|tsx)\b", re.I), ["react", "vite"]),
    (re.compile(r"\b(next(\.js|js)?|app\s*router)\b", re.I), ["nextjs"]),
    (re.compile(r"\b(vue(\.js)?|composition\s*api)\b", re.I), ["vue", "vite"]),
    (re.compile(r"\b(svelte(kit)?)\b", re.I), ["svelte"]),
    (re.compile(r"\b(tailwind(\s*css)?)\b", re.I), ["tailwind"]),
    (re.compile(r"\bvite\b", re.I), ["vite"]),

    # Stacks — backend
    (re.compile(r"\bfastapi\b", re.I), ["fastapi", "api"]),
    (re.compile(r"\bflask\b", re.I), ["flask"]),
    (re.compile(r"\bdjango\b", re.I), ["django"]),
    (re.compile(r"\b(express(\.js)?|expressjs)\b", re.I), ["express", "api"]),
    (re.compile(r"\b(nest(\.js|js)?|nestjs)\b", re.I), ["nestjs", "api"]),

    # CLI / desktop
    (re.compile(r"\bpython\s+cli\b|\bcli\s+(in|with)\s+python\b", re.I), ["python-cli"]),
    (re.compile(r"\bnode\s+cli\b|\bcli\s+(in|with)\s+(node|typescript|ts)\b", re.I), ["node-cli"]),
    (re.compile(r"\b(cli|command[-\s]?line)\b", re.I), ["python-cli"]),  # default CLI = python-cli
    (re.compile(r"\belectron\b", re.I), ["electron"]),
]


# When two skills cover overlapping ground, the more specific wins.
# Each pair is (more_specific, less_specific): if both matched, drop the
# less specific. Keep the list small and obvious.
_SKILL_CONFLICTS = [
    ("node-cli", "python-cli"),    # node-cli explicit beats the python-cli default
    ("nextjs", "react"),           # Next includes React; the Next skill covers conventions
    ("nestjs", "express"),         # Nest is built on Express; Nest skill is enough
]


_SKILL_PRIORITY = {
    "system": 0,
    "modern-ui": 1,
    "landing-page": 2,
    "dashboard": 2,
    "saas": 2,
    "api": 2,
    "react": 3, "nextjs": 3, "vue": 3, "svelte": 3, "vite": 4,
    "tailwind": 4,
    "fastapi": 3, "flask": 3, "django": 3, "express": 3, "nestjs": 3,
    "python-cli": 3, "node-cli": 3, "electron": 3,
}


def _read_skill_file(name: str) -> str | None:
    skills_dir = load_settings().get(
        "skills_dir", os.path.join(".kryth", "skills")
    )
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
    skills_dir = load_settings().get(
        "skills_dir", os.path.join(".kryth", "skills")
    )
    if os.path.isdir(skills_dir):
        for f in os.listdir(skills_dir):
            if f.endswith(".md"):
                names.add(f[:-3])
    return sorted(names)


def _is_build_request(user_input: str) -> bool:
    """True if the prompt looks like an instruction to build/create
    something, as opposed to a question or a small follow-up.
    """
    return bool(_BUILD_VERBS.search(user_input))


def auto_select_skills(user_input: str) -> list[str]:
    """Return skill names matching ``user_input`` based on keyword rules.

    Returns an empty list when:
    - input is empty or starts with ``/`` (explicit /skill invocation),
    - or there is no build verb (questions / follow-ups).

    Always prepends ``system`` when any stack/archetype matches, so a
    "build a flask blog" picks up the master quality bar even though
    "blog" isn't in any skill's regex.
    """
    if not user_input or user_input.lstrip().startswith("/"):
        return []
    if not _is_build_request(user_input):
        return []

    selected: set[str] = set()
    for pattern, skills in AUTO_SKILL_RULES:
        if pattern.search(user_input):
            selected.update(skills)

    # The master "system" skill always applies to build requests — the
    # archetype/stack matches just add specificity on top. Even a bare
    # "build a flask blog" should get the quality bar.
    selected.add("system")

    for specific, generic in _SKILL_CONFLICTS:
        if specific in selected and generic in selected:
            selected.discard(generic)

    return sorted(selected, key=lambda n: (_SKILL_PRIORITY.get(n, 9), n))


def compose_skills(names: list[str]) -> str:
    """Concatenate skill bodies for injection as a single extra_system.

    Bounded by ``COMPOSED_BUDGET_CHARS`` so multi-skill matches don't
    silently inflate the prompt by several kilobytes. The ``system``
    master skill is always included in full; other skills share the
    remaining budget. Each non-essential skill is trimmed at the
    nearest paragraph boundary above its allotted share, with a clear
    truncation marker.
    """
    if not names:
        return ""

    essential: list[str] = []
    others: list[str] = []
    for n in names:
        (essential if n in _ESSENTIAL_SKILLS else others).append(n)

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
                chunk = _trim_to_paragraph(chunk, per_skill, label=n)
            parts.append(chunk)

    return "\n\n".join(parts)


# Skills that should never be truncated regardless of how many others
# matched — these carry the system-wide quality bar and process steps.
_ESSENTIAL_SKILLS = {"system"}

# Total character budget for ``compose_skills`` (~1k tokens). Tuned for
# a model with 128k+ context where this is rounding error individually
# but compounds across turns when a single request matches 4-6 skills.
COMPOSED_BUDGET_CHARS = 4000

# Floor per-skill so a busy match doesn't trim every non-essential
# skill into uselessness.
_MIN_PER_SKILL_CHARS = 400


def _trim_to_paragraph(text: str, budget: int, *, label: str) -> str:
    """Cut ``text`` to roughly ``budget`` chars at a paragraph boundary,
    appending a truncation marker. Falls back to a hard cut if no
    paragraph break is reachable within the budget."""
    cut = text.rfind("\n\n", 0, budget)
    if cut < budget // 2:
        cut = budget
    return text[:cut].rstrip() + f"\n\n[…{label} skill trimmed for prompt budget]"
