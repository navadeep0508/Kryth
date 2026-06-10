"""Remote registry client — queries skills.kryth.dev (or GitHub fallback)."""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional

from agent.ecosystem.models import SkillPackage
from agent.env import home_dir


_REGISTRY_URL  = "https://skills.kryth.dev/api"
_CACHE_FILE    = home_dir() / "skills" / "registry_cache.json"
_CACHE_TTL_SEC = 3600  # 1 hour

# GitHub fallback: skills published as kryth-skills/<id> repos
_GITHUB_ORG   = "kryth-skills"
_GITHUB_RAW   = "https://raw.githubusercontent.com"

# Built-in skill metadata for skills that ship as prompt fragments.
# These are always available without downloading anything.
_BUILTIN_SKILLS: List[dict] = [
    {"id": "react-builder",       "name": "React Builder",       "version": "1.0.0", "description": "Build complete React apps with TypeScript, hooks, and best practices",         "author": "KRYTH", "tags": ["react", "frontend", "typescript"], "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": ["tailwind-designer"]},
    {"id": "nextjs-builder",      "name": "Next.js Builder",     "version": "1.0.0", "description": "Full-stack Next.js 14+ apps with App Router, server actions, and TypeScript",  "author": "KRYTH", "tags": ["nextjs", "react", "fullstack"],    "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": ["tailwind-designer"]},
    {"id": "fastapi-builder",     "name": "FastAPI Builder",     "version": "1.0.0", "description": "Production-ready FastAPI services with Pydantic, async, and OpenAPI docs",      "author": "KRYTH", "tags": ["fastapi", "python", "api"],        "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "tailwind-designer",   "name": "Tailwind Designer",   "version": "1.0.0", "description": "Beautiful Tailwind CSS designs with responsive layouts and dark mode",         "author": "KRYTH", "tags": ["tailwind", "css", "design"],      "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "auth-builder",        "name": "Auth Builder",        "version": "1.0.0", "description": "JWT + OAuth authentication with role-based access control",                    "author": "KRYTH", "tags": ["auth", "security", "jwt"],        "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "database-designer",   "name": "Database Designer",   "version": "1.0.0", "description": "PostgreSQL/SQLite schema design with migrations and ORM setup",                "author": "KRYTH", "tags": ["database", "sql", "orm"],         "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "payment-integrator",  "name": "Payment Integrator",  "version": "1.0.0", "description": "Stripe payment integration with webhooks and subscription management",         "author": "KRYTH", "tags": ["payments", "stripe", "saas"],     "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "docker-builder",      "name": "Docker Builder",      "version": "1.0.0", "description": "Production Dockerfile + compose with multi-stage builds and health checks",    "author": "KRYTH", "tags": ["docker", "devops", "deployment"],  "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem", "terminal"], "verified": True, "chains": []},
    {"id": "test-generator",      "name": "Test Generator",      "version": "1.0.0", "description": "Comprehensive unit + integration tests with high coverage",                     "author": "KRYTH", "tags": ["testing", "pytest", "jest"],       "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem", "terminal"], "verified": True, "chains": []},
    {"id": "landing-page-builder","name": "Landing Page Builder","version": "1.0.0", "description": "High-converting landing pages with hero, features, CTA, and animations",       "author": "KRYTH", "tags": ["landing", "marketing", "ui"],     "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": ["tailwind-designer"]},
    {"id": "saas-builder",        "name": "SaaS Builder",        "version": "1.0.0", "description": "Full SaaS platform with auth, billing, dashboard, and multi-tenancy",          "author": "KRYTH", "tags": ["saas", "fullstack", "billing"],    "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": ["auth-builder", "payment-integrator", "database-designer"]},
    {"id": "api-builder",         "name": "API Builder",         "version": "1.0.0", "description": "RESTful API with OpenAPI spec, rate limiting, and versioning",                 "author": "KRYTH", "tags": ["api", "rest", "openapi"],         "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "bug-fixer",           "name": "Bug Fixer",           "version": "1.0.0", "description": "Deep code analysis and systematic bug fixing with root cause diagnosis",        "author": "KRYTH", "tags": ["debug", "fix", "analysis"],       "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem", "terminal"], "verified": True, "chains": []},
    {"id": "readme-generator",    "name": "README Generator",    "version": "1.0.0", "description": "Professional README with badges, architecture diagrams, and usage examples",   "author": "KRYTH", "tags": ["docs", "readme", "markdown"],     "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "code-reviewer",       "name": "Code Reviewer",       "version": "1.0.0", "description": "Senior engineer code review: bugs, security, performance, and best practices", "author": "KRYTH", "tags": ["review", "quality", "security"],  "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "vue-builder",         "name": "Vue Builder",         "version": "1.0.0", "description": "Vue 3 + Pinia + TypeScript apps with Composition API",                         "author": "KRYTH", "tags": ["vue", "frontend", "typescript"],  "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": ["tailwind-designer"]},
    {"id": "django-builder",      "name": "Django Builder",      "version": "1.0.0", "description": "Django projects with DRF, admin, migrations, and Celery",                      "author": "KRYTH", "tags": ["django", "python", "backend"],    "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "electron-builder",    "name": "Electron Builder",    "version": "1.0.0", "description": "Cross-platform desktop apps with Electron and React",                          "author": "KRYTH", "tags": ["electron", "desktop", "react"],   "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "rag-builder",         "name": "RAG Builder",         "version": "1.0.0", "description": "Retrieval-Augmented Generation system with vector DB and LLM integration",     "author": "KRYTH", "tags": ["ai", "rag", "llm", "vector"],     "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem"], "verified": True, "chains": []},
    {"id": "deployment-manager",  "name": "Deployment Manager",  "version": "1.0.0", "description": "Deploy to Vercel, AWS, GCP, or Railway with CI/CD pipelines",                 "author": "KRYTH", "tags": ["deploy", "devops", "cloud"],      "entry": "prompt.md", "dependencies": [], "permissions": ["filesystem", "terminal"], "verified": True, "chains": ["docker-builder"]},
]


class RemoteRegistry:
    """Client for the skills.kryth.dev registry with local cache."""

    def __init__(self) -> None:
        self._cache: Dict[str, dict] | None = None
        self._cache_time: float = 0.0

    # ── Cache ──────────────────────────────────────────────────────────────

    def _load_cache(self) -> Dict[str, dict] | None:
        if not _CACHE_FILE.exists():
            return None
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if time.time() - data.get("_ts", 0) < _CACHE_TTL_SEC:
                return data.get("skills", {})
        except Exception:
            pass
        return None

    def _save_cache(self, skills: Dict[str, dict]) -> None:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"_ts": time.time(), "skills": skills}, f, indent=2)
        except Exception:
            pass

    # ── Remote fetch ───────────────────────────────────────────────────────

    def _fetch_remote(self, timeout: int = 5) -> Dict[str, dict]:
        try:
            req = urllib.request.Request(
                f"{_REGISTRY_URL}/skills",
                headers={"User-Agent": "kryth/1.1", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {s["id"]: s for s in data.get("skills", [])}
        except Exception:
            return {}

    # ── Built-ins ─────────────────────────────────────────────────────────

    def _builtin_map(self) -> Dict[str, dict]:
        return {s["id"]: s for s in _BUILTIN_SKILLS}

    # ── Public API ─────────────────────────────────────────────────────────

    def list_skills(self, force_refresh: bool = False) -> List[SkillPackage]:
        """Return all available skills (built-ins + remote, cached)."""
        skills = dict(self._builtin_map())

        if force_refresh or self._cache is None:
            cached = self._load_cache()
            if cached and not force_refresh:
                self._cache = cached
            else:
                remote = self._fetch_remote()
                if remote:
                    skills.update(remote)
                    self._save_cache(skills)
                    self._cache = skills
                else:
                    self._cache = skills
        else:
            skills.update(self._cache)

        return [SkillPackage.from_dict(d) for d in skills.values()]

    def find(self, skill_id: str) -> Optional[SkillPackage]:
        """Fetch metadata for a single skill by ID."""
        # Check built-ins first
        builtins = self._builtin_map()
        if skill_id in builtins:
            return SkillPackage.from_dict(builtins[skill_id])

        # Try remote
        try:
            req = urllib.request.Request(
                f"{_REGISTRY_URL}/skills/{skill_id}",
                headers={"User-Agent": "kryth/1.1", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return SkillPackage.from_dict(json.loads(resp.read().decode("utf-8")))
        except Exception:
            pass

        # GitHub fallback: look for kryth-skills/<id>/skill.json
        try:
            url = f"{_GITHUB_RAW}/{_GITHUB_ORG}/{skill_id}/main/skill.json"
            req = urllib.request.Request(url, headers={"User-Agent": "kryth/1.1"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                d = json.loads(resp.read().decode("utf-8"))
                d.setdefault("id", skill_id)
                d.setdefault("download_url",
                    f"https://github.com/{_GITHUB_ORG}/{skill_id}/archive/refs/heads/main.zip")
                return SkillPackage.from_dict(d)
        except Exception:
            pass

        return None

    def search(self, query: str) -> List[SkillPackage]:
        q = query.lower()
        return [
            s for s in self.list_skills()
            if q in s.id.lower()
            or q in s.name.lower()
            or q in s.description.lower()
            or any(q in t for t in s.tags)
        ]


_remote: RemoteRegistry | None = None


def get_remote_registry() -> RemoteRegistry:
    global _remote
    if _remote is None:
        _remote = RemoteRegistry()
    return _remote
