"""AI-powered skill router — maps user intent to required skill IDs."""

from __future__ import annotations

import json
from typing import List

from agent.ecosystem.remote_registry import get_remote_registry


# ---------------------------------------------------------------------------
# Keyword fallback rules (fast path — no LLM call needed)
# ---------------------------------------------------------------------------

_KEYWORD_RULES: list[tuple[list[str], list[str]]] = [
    # Frontend
    (["react", "jsx", "tsx"],                         ["react-builder"]),
    (["next.js", "nextjs", "app router"],             ["nextjs-builder"]),
    (["vue", "nuxt"],                                 ["vue-builder"]),
    (["svelte", "sveltekit"],                         ["vue-builder"]),
    (["electron", "desktop app"],                     ["electron-builder"]),
    (["tailwind"],                                    ["tailwind-designer"]),
    (["landing page", "marketing page"],              ["landing-page-builder"]),
    (["dashboard"],                                   ["nextjs-builder", "tailwind-designer"]),

    # Backend
    (["fastapi", "fast api"],                         ["fastapi-builder"]),
    (["django"],                                      ["django-builder"]),
    (["flask"],                                       ["fastapi-builder"]),
    (["express", "node api", "nodejs api"],           ["api-builder"]),
    (["rest api", "restful", "openapi"],              ["api-builder"]),

    # Features
    (["auth", "login", "signup", "oauth", "jwt"],     ["auth-builder"]),
    (["payment", "stripe", "billing", "subscribe"],   ["payment-integrator"]),
    (["database", "postgres", "sqlite", "mysql"],     ["database-designer"]),
    (["docker", "containerize", "container"],         ["docker-builder"]),
    (["deploy", "deployment", "vercel", "aws"],       ["deployment-manager"]),
    (["test", "testing", "unit test", "coverage"],    ["test-generator"]),
    (["readme", "documentation", "docs"],             ["readme-generator"]),
    (["review", "code review", "audit"],              ["code-reviewer"]),
    (["rag", "vector", "embedding", "llm app"],       ["rag-builder"]),

    # Project types
    (["saas", "subscription app", "multi-tenant"],    ["saas-builder"]),
    (["ecommerce", "e-commerce", "shop", "store"],    ["saas-builder", "payment-integrator"]),
    (["crm", "customer relationship"],                ["saas-builder", "database-designer"]),
    (["blog", "cms", "content management"],           ["nextjs-builder", "database-designer"]),
    (["chat app", "messaging", "realtime"],           ["fastapi-builder", "react-builder"]),
    (["portfolio", "personal site"],                  ["landing-page-builder"]),
]


def _keyword_route(user_input: str) -> List[str]:
    text = user_input.lower()
    matched: set[str] = set()
    for keywords, skills in _KEYWORD_RULES:
        if any(kw in text for kw in keywords):
            matched.update(skills)
    return sorted(matched)


# ---------------------------------------------------------------------------
# LLM-powered routing
# ---------------------------------------------------------------------------

_ROUTER_SYSTEM = """You are a skill router for KRYTH AI coding agent.
Given a user request, identify which skill IDs are needed to fulfill it.

Available skills:
{skill_list}

Rules:
- Return ONLY a JSON array of skill IDs, nothing else.
- Include 1-6 skills maximum. Less is more.
- Only include skills that are genuinely needed for the specific request.
- For full-stack apps include both frontend and backend skills.
- Always include tailwind-designer when there's any UI work.
- For SaaS/ecommerce, include auth-builder and database-designer.
- Consider the user's intent carefully — don't over-select. If the request is simple, pick fewer skills.
- If the request mentions a specific technology (e.g., "React", "FastAPI", "Next.js"), include the corresponding builder skill.

Example output: ["react-builder", "tailwind-designer", "fastapi-builder"]"""


def _llm_route(user_input: str) -> List[str]:
    try:
        from agent.llm import _get_client, PLANNER_MODEL
        registry = get_remote_registry()
        skills = registry.list_skills()
        skill_list = "\n".join(
            f"- {s.id}: {s.description}" for s in skills
        )
        # Build set of valid skill IDs for validation
        valid_ids = {s.id for s in skills}
        client = _get_client()
        response = client.chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM.format(skill_list=skill_list)},
                {"role": "user", "content": user_input},
            ],
            temperature=0,
            max_tokens=200,
            timeout=8,  # never block the UI for more than 8s
        )
        text = (response.choices[0].message.content or "").strip()
        # Extract JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            ids = json.loads(text[start:end + 1])
            if isinstance(ids, list):
                # Validate: only keep IDs that exist in the registry
                validated = [str(s) for s in ids if str(s) in valid_ids]
                return validated
    except Exception:
        pass
    return []


class SkillRouter:
    """Determines which skills are needed for a user request."""

    def route(self, user_input: str, use_llm: bool = True) -> List[str]:
        """Return ordered list of skill IDs needed for the request.

        Uses LLM routing first (smarter), falls back to keyword matching.
        Only routes build requests; non-build queries return empty.
        """
        if not user_input or user_input.startswith("/"):
            return []
        if not self.is_build_request(user_input):
            return []
        if use_llm:
            ids = _llm_route(user_input)
            if ids:
                return ids
        return _keyword_route(user_input)

    def is_build_request(self, user_input: str) -> bool:
        """True if the prompt is likely a build/create request."""
        import re
        verbs = re.compile(
            r"\b(build|create|make|generate|scaffold|set\s*up|bootstrap|"
            r"implement|develop|design|write|code|ship|start|new|init)\b",
            re.I,
        )
        return bool(verbs.search(user_input))


_router: SkillRouter | None = None


def get_router() -> SkillRouter:
    global _router
    if _router is None:
        _router = SkillRouter()
    return _router


def route(user_input: str, use_llm: bool = True) -> List[str]:
    """Module-level route function for backward compatibility.

    Delegates to the singleton router instance.
    """
    return get_router().route(user_input, use_llm)
