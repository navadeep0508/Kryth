"""Skill installer — download, verify, and install skill packages."""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

from agent.ecosystem.models import SkillPackage
from agent.ecosystem.local_registry import get_local_registry
from agent.ecosystem.remote_registry import get_remote_registry, _GITHUB_ORG, _GITHUB_RAW

# Built-in prompt fragments for official skills (shipped with KRYTH).
# Keys match skill IDs in remote_registry._BUILTIN_SKILLS.
_BUILTIN_PROMPTS: dict[str, str] = {
    "react-builder": (
        "[Skill: react-builder]\nBuild production-quality React applications.\n"
        "Use TypeScript, functional components, hooks, and Vite as the bundler.\n"
        "Structure: src/components/, src/hooks/, src/pages/, src/utils/.\n"
        "State: useState/useContext for local, Zustand for global.\n"
        "Styling: Tailwind CSS with clsx for conditional classes.\n"
        "Always add proper TypeScript types. No any types.\n"
        "Include error boundaries and loading states."
    ),
    "nextjs-builder": (
        "[Skill: nextjs-builder]\nBuild full-stack Next.js 14+ applications.\n"
        "Use App Router, Server Components, Server Actions, and TypeScript.\n"
        "Structure: app/, components/, lib/, types/.\n"
        "Data: Server Components for fetching, React Query for client-side.\n"
        "Styling: Tailwind CSS + shadcn/ui components.\n"
        "Auth: NextAuth.js or Clerk.\n"
        "DB: Prisma with PostgreSQL or Supabase."
    ),
    "fastapi-builder": (
        "[Skill: fastapi-builder]\nBuild production FastAPI services.\n"
        "Use Pydantic v2, async/await, and SQLAlchemy 2.0 async.\n"
        "Structure: app/api/, app/models/, app/schemas/, app/services/.\n"
        "Include OpenAPI docs, health endpoint, CORS config, and rate limiting.\n"
        "Auth: JWT with python-jose. Passwords: bcrypt.\n"
        "Tests: pytest-asyncio with httpx.AsyncClient."
    ),
    "tailwind-designer": (
        "[Skill: tailwind-designer]\nCreate beautiful Tailwind CSS designs.\n"
        "Use a consistent design system: spacing scale, color palette, typography.\n"
        "Every UI must be fully responsive (mobile-first).\n"
        "Add hover/focus/active states to all interactive elements.\n"
        "Include dark mode support with dark: variants.\n"
        "Use animations sparingly: transition-all, hover:scale.\n"
        "Write real copy — no lorem ipsum."
    ),
    "auth-builder": (
        "[Skill: auth-builder]\nImplement secure authentication.\n"
        "JWT access + refresh tokens. Bcrypt password hashing.\n"
        "Include: register, login, logout, refresh, forgot-password flows.\n"
        "Role-based access control (RBAC) with guard middleware.\n"
        "Rate limiting on auth endpoints. Secure httpOnly cookies.\n"
        "OAuth: Google + GitHub providers."
    ),
    "database-designer": (
        "[Skill: database-designer]\nDesign and implement database schemas.\n"
        "PostgreSQL first choice. SQLite for simple apps.\n"
        "Use migrations (Alembic for Python, Prisma Migrate for JS).\n"
        "Index all foreign keys and frequently queried columns.\n"
        "Include soft deletes, created_at/updated_at timestamps.\n"
        "Use transactions for multi-step operations."
    ),
    "payment-integrator": (
        "[Skill: payment-integrator]\nIntegrate Stripe payments.\n"
        "Include: one-time payments, subscriptions, and webhooks.\n"
        "Use Stripe Checkout or Elements for the UI.\n"
        "Handle: payment_intent.succeeded, invoice.payment_failed, customer.subscription.deleted.\n"
        "Store: Stripe customer ID, subscription ID, plan tier.\n"
        "Include a billing portal for subscription management."
    ),
    "docker-builder": (
        "[Skill: docker-builder]\nCreate production Docker configurations.\n"
        "Multi-stage Dockerfile: build stage + slim runtime stage.\n"
        "Non-root user in container. Health check endpoint.\n"
        "docker-compose.yml with: app, db, redis, nginx services.\n"
        "Environment variables via .env file (never hardcode secrets).\n"
        "Include .dockerignore. Optimize layer caching."
    ),
    "test-generator": (
        "[Skill: test-generator]\nWrite comprehensive test suites.\n"
        "Python: pytest + pytest-cov. Target >80% coverage.\n"
        "JS/TS: Vitest or Jest. React: Testing Library.\n"
        "Include: unit tests, integration tests, edge cases, error paths.\n"
        "Mock external services. Test async code properly.\n"
        "Add a CI-ready test script to package.json or Makefile."
    ),
    "saas-builder": (
        "[Skill: saas-builder]\nBuild complete SaaS platforms.\n"
        "Include: user auth, team/org model, subscription billing, admin dashboard.\n"
        "Multi-tenancy: row-level security or schema-per-tenant.\n"
        "Onboarding: email verification, welcome flow, empty states.\n"
        "Usage limits tied to plan tier.\n"
        "Analytics: track key events (signup, upgrade, churn)."
    ),
    "landing-page-builder": (
        "[Skill: landing-page-builder]\nBuild high-converting landing pages.\n"
        "Sections: hero, features (3+), social proof, pricing, FAQ, CTA, footer.\n"
        "Hero: clear value prop, subheadline, CTA button, hero image/video.\n"
        "Social proof: logos, testimonials with photos, metrics.\n"
        "Animations: scroll-triggered reveals with Intersection Observer.\n"
        "Performance: above-fold content loads <1s. Images lazy-loaded."
    ),
    "api-builder": (
        "[Skill: api-builder]\nBuild RESTful APIs following OpenAPI 3.0.\n"
        "Versioned endpoints: /api/v1/.\n"
        "Standard responses: {data, error, meta, pagination}.\n"
        "Rate limiting, request validation, and error handling middleware.\n"
        "Authentication: Bearer token or API key.\n"
        "Auto-generated OpenAPI/Swagger docs."
    ),
    "bug-fixer": (
        "[Skill: bug-fixer]\nSystematically diagnose and fix bugs.\n"
        "1. Reproduce: identify the exact failing case.\n"
        "2. Trace: follow the execution path to find root cause.\n"
        "3. Fix: minimal, targeted change. No unrelated cleanup.\n"
        "4. Test: add a test that would have caught this bug.\n"
        "5. Document: comment explaining WHY, not what.\n"
        "Never mask errors — fix root causes."
    ),
    "code-reviewer": (
        "[Skill: code-reviewer]\nReview code as a senior engineer.\n"
        "Check: correctness, security vulnerabilities, performance, edge cases.\n"
        "Flag: N+1 queries, SQL injection risks, XSS vectors, race conditions.\n"
        "Suggest: simplifications, better abstractions, naming improvements.\n"
        "Format findings as: [BUG/RISK/SUGGESTION] file:line — description."
    ),
    "readme-generator": (
        "[Skill: readme-generator]\nWrite professional README files.\n"
        "Sections: badges, title, description, features, quickstart, usage,\n"
        "API reference, configuration, contributing, license.\n"
        "Include actual runnable commands. No placeholder text.\n"
        "Add architecture diagram (ASCII or Mermaid).\n"
        "Keep quickstart under 3 commands."
    ),
    "vue-builder": (
        "[Skill: vue-builder]\nBuild Vue 3 applications.\n"
        "Use Composition API, <script setup>, TypeScript, and Pinia.\n"
        "Structure: src/components/, src/views/, src/stores/, src/composables/.\n"
        "Router: Vue Router 4 with route guards.\n"
        "Bundler: Vite. Styling: Tailwind CSS."
    ),
    "django-builder": (
        "[Skill: django-builder]\nBuild Django projects.\n"
        "Use Django 4.2+, Django REST Framework, and Celery.\n"
        "Structure: apps/ per domain. Custom user model from start.\n"
        "Admin: customized with list_display, search, filters.\n"
        "API: DRF serializers + ViewSets. Auth: JWT with djangorestframework-simplejwt."
    ),
    "electron-builder": (
        "[Skill: electron-builder]\nBuild Electron desktop apps.\n"
        "Main process + renderer process with contextBridge.\n"
        "React + TypeScript in renderer. Vite as bundler.\n"
        "IPC: typed channels with ipcMain/ipcRenderer.\n"
        "Auto-update: electron-updater. Packaging: electron-builder."
    ),
    "rag-builder": (
        "[Skill: rag-builder]\nBuild RAG (Retrieval-Augmented Generation) systems.\n"
        "Vector DB: ChromaDB (local) or Pinecone (cloud).\n"
        "Embeddings: OpenAI text-embedding-3-small.\n"
        "Pipeline: ingest → chunk → embed → store → retrieve → augment → generate.\n"
        "Chunking: 512 tokens with 50-token overlap.\n"
        "Reranking: cross-encoder for precision."
    ),
    "deployment-manager": (
        "[Skill: deployment-manager]\nSet up deployment pipelines.\n"
        "Vercel: vercel.json with routes and env vars.\n"
        "Railway: railway.toml.\n"
        "AWS: ECS Fargate with ALB or Lambda + API Gateway.\n"
        "GitHub Actions: build → test → deploy workflow.\n"
        "Include rollback strategy and health checks."
    ),
}


ProgressCallback = Callable[[str, str], None]  # (skill_id, status)


class SkillInstaller:
    """Downloads and installs skill packages to the local registry."""

    def __init__(self, progress: ProgressCallback | None = None) -> None:
        self._progress = progress or (lambda _id, _s: None)
        self._local = get_local_registry()
        self._remote = get_remote_registry()

    def _notify(self, skill_id: str, status: str) -> None:
        self._progress(skill_id, status)

    def ensure_installed(self, skill_id: str) -> Optional[SkillPackage]:
        """Install skill if not already present. Returns the package or None."""
        if self._local.has(skill_id):
            self._notify(skill_id, "cached")
            return self._local.get(skill_id)

        # Look up metadata
        pkg = self._remote.find(skill_id)
        if not pkg:
            self._notify(skill_id, "not_found")
            return None

        return self._install(pkg)

    def _install(self, pkg: SkillPackage) -> Optional[SkillPackage]:
        self._notify(pkg.id, "installing")

        skill_dir = self._local.skill_dir(pkg.id)
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Official built-in skills have prompt text embedded in this module
        if pkg.id in _BUILTIN_PROMPTS:
            prompt_path = skill_dir / "prompt.md"
            prompt_path.write_text(_BUILTIN_PROMPTS[pkg.id], encoding="utf-8")
            pkg.install_path = str(skill_dir)
            pkg.entry = "prompt.md"
            pkg.verified = True
            self._local.register(pkg)
            self._notify(pkg.id, "installed")
            return pkg

        # Community skill — download from URL
        if pkg.download_url:
            success = self._download_zip(pkg, skill_dir)
            if success:
                pkg.install_path = str(skill_dir)
                self._local.register(pkg)
                self._notify(pkg.id, "installed")
                return pkg

        self._notify(pkg.id, "failed")
        return None

    def _download_zip(self, pkg: SkillPackage, dest: Path) -> bool:
        import urllib.request
        try:
            self._notify(pkg.id, "downloading")
            req = urllib.request.Request(
                pkg.download_url,
                headers={"User-Agent": "kryth/1.1"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                # Zip may have a top-level directory — strip it
                names = zf.namelist()
                prefix = names[0] if names and names[0].endswith("/") else ""
                for member in names:
                    rel = member[len(prefix):]
                    if not rel:
                        continue
                    target = dest / rel
                    if member.endswith("/"):
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(member))
            return True
        except Exception:
            return False

    def install_all(self, skill_ids: List[str]) -> dict[str, Optional[SkillPackage]]:
        """Install multiple skills. Returns {id: package_or_None}."""
        return {sid: self.ensure_installed(sid) for sid in skill_ids}


def get_installer(progress: ProgressCallback | None = None) -> SkillInstaller:
    return SkillInstaller(progress=progress)
