"""Parallel multi-agent builder — preset-based.

The AI picks a preset number based on the user request.
Each preset defines a fixed set of parallel agents for a specific
type of build. No LLM overhead to decide agent structure — just
match the request to a preset and run it.

Presets:
  1  — Website (HTML + CSS + JS)
  2  — Full-Stack Web App (Frontend + Backend + DB)
  3  — SaaS Platform (Frontend + Backend + Auth + Payments + DevOps)
  4  — REST API / Backend Service
  5  — ML / AI Engineering (Data pipeline + Model + API + UI)
  6  — Data Science (EDA + Feature Eng + Model + Report)
  7  — Automation / Scripting (Core logic + CLI + Config + Tests)
  8  — React / Next.js App (Components + Pages + API + Styles)
  9  — Python Package / Library (Core + CLI + Tests + Docs)
  10 — DevOps / Infrastructure (Docker + CI/CD + Nginx + Monitoring)
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

from agent import ui
from agent.ui.streaming import set_parallel_mode


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FeatureSpec:
    id: str
    name: str
    description: str
    depends_on: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)


@dataclass
class ProjectSpec:
    project_name: str
    description: str
    tech_stack: dict
    features: List[FeatureSpec]
    integration_notes: str = ""
    preset_id: int = 0

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectSpec":
        return cls(
            project_name=d.get("project_name", "project"),
            description=d.get("description", ""),
            tech_stack=d.get("tech_stack", {}),
            features=[
                FeatureSpec(
                    id=a.get("id", str(i)),
                    name=a.get("name", f"Agent {i}"),
                    description=a.get("description", ""),
                    depends_on=a.get("depends_on", []),
                    files=a.get("files", []),
                )
                for i, a in enumerate(d.get("agents", []))
            ],
            integration_notes=d.get("integration_notes", ""),
            preset_id=d.get("preset_id", 0),
        )


# ---------------------------------------------------------------------------
# Presets — fixed agent structures for each build type
# ---------------------------------------------------------------------------

PRESETS: Dict[int, dict] = {
    1: {
        "name": "Website",
        "description": "Static website with HTML, CSS, and JavaScript",
        "keywords": ["website", "webpage", "landing page", "portfolio", "html", "css", "static site", "personal site"],
        "agents": [
            {
                "id": "html",
                "name": "HTML Structure",
                "description": (
                    "Build the complete HTML structure. Create all pages with semantic HTML5, "
                    "real content (no Lorem ipsum), navigation, sections, and proper meta tags. "
                    "Include all the content the user asked for. Write production-ready HTML."
                ),
                "files": ["index.html", "*.html"],
            },
            {
                "id": "styles",
                "name": "CSS Styles",
                "description": (
                    "Build all stylesheets. Create modern CSS/SCSS with: real color palette, "
                    "typography, flexbox/grid layouts, animations, hover states, responsive mobile design. "
                    "Make it visually polished and professional."
                ),
                "files": ["styles.css", "style.css", "*.css", "*.scss"],
            },
            {
                "id": "scripts",
                "name": "JavaScript",
                "description": (
                    "Build all JavaScript. Add real interactivity: smooth scroll, mobile menu, "
                    "form validation, animations, carousels, dark mode toggle, or whatever the "
                    "site needs. Wire all buttons and interactive elements."
                ),
                "files": ["script.js", "main.js", "*.js"],
            },
        ],
        "integration_notes": "Link CSS in <head>, JS before </body>. Use consistent class names.",
    },

    2: {
        "name": "Full-Stack Web App",
        "description": "Full-stack app with frontend, backend API, and database",
        "keywords": ["full stack", "fullstack", "web app", "webapp", "react", "vue", "angular", "next.js", "express", "fastapi", "flask", "django", "node", "app"],
        "agents": [
            {
                "id": "backend",
                "name": "Backend API",
                "description": (
                    "Build the complete backend. Set up Express/FastAPI/Django with: "
                    "all REST API routes, auth middleware (JWT), database models/migrations, "
                    "error handling, CORS, environment config (.env.example), package.json/requirements.txt. "
                    "Run on port 4000. Write complete working code — no stubs."
                ),
                "files": ["backend/"],
            },
            {
                "id": "frontend",
                "name": "Frontend UI",
                "description": (
                    "Build the complete frontend. Set up React/Vue/Next.js with: "
                    "all pages and components, routing, API integration pointing to http://localhost:4000, "
                    "state management, auth flow (login/register), responsive Tailwind styling, "
                    "package.json with all deps. Write complete working code — no stubs."
                ),
                "files": ["frontend/"],
            },
            {
                "id": "database",
                "name": "Database Schema",
                "description": (
                    "Design and implement the database layer. Write complete Prisma schema / "
                    "SQLAlchemy models / Django models with all tables, relationships, indexes. "
                    "Include seed data script and migration setup. Write complete working code."
                ),
                "files": ["prisma/", "database/", "migrations/"],
            },
        ],
        "integration_notes": "Frontend at port 3000, backend at 4000. Frontend proxies /api to backend.",
    },

    3: {
        "name": "SaaS Platform",
        "description": "Production SaaS with auth, payments, admin, and DevOps",
        "keywords": ["saas", "subscription", "platform", "multi-tenant", "stripe", "payments", "billing", "admin panel", "dashboard"],
        "agents": [
            {
                "id": "frontend",
                "name": "Frontend (React/Next.js)",
                "description": (
                    "Build the complete SaaS frontend with Next.js/React: landing page, "
                    "auth pages (login/register/forgot), user dashboard, pricing page, "
                    "subscription management UI, admin panel, Tailwind CSS styling, "
                    "React Query for data fetching. All pages fully implemented."
                ),
                "files": ["frontend/", "app/"],
            },
            {
                "id": "backend",
                "name": "Backend API",
                "description": (
                    "Build the complete SaaS backend: user auth (JWT + refresh tokens), "
                    "role-based access (user/admin), subscription management, Stripe webhook handler, "
                    "API routes for all features, rate limiting, email sending setup, "
                    "full error handling. Port 4000."
                ),
                "files": ["backend/", "server/"],
            },
            {
                "id": "payments",
                "name": "Payments & Subscriptions",
                "description": (
                    "Build the complete Stripe integration: payment intents, subscription plans, "
                    "webhook handling (checkout.session.completed, invoice.paid, customer.subscription.deleted), "
                    "customer portal, trial periods, plan upgrade/downgrade logic. "
                    "Include Stripe test keys in .env.example."
                ),
                "files": ["backend/payments/", "backend/webhooks/", "backend/stripe/"],
            },
            {
                "id": "devops",
                "name": "DevOps & Infrastructure",
                "description": (
                    "Build complete infrastructure: docker-compose.yml (postgres, redis, backend, frontend, nginx), "
                    "Nginx config, GitHub Actions CI/CD pipeline, .env.example for all services, "
                    "Dockerfile for each service, health check endpoints, README with deployment guide."
                ),
                "files": ["docker-compose.yml", "Dockerfile*", ".github/", "nginx/"],
            },
        ],
        "integration_notes": "Frontend port 3000, backend 4000, postgres 5432, redis 6379. Stripe webhooks at /api/webhooks/stripe.",
    },

    4: {
        "name": "REST API / Backend Service",
        "description": "Backend API service with routes, auth, and database",
        "keywords": ["api", "rest api", "backend", "server", "microservice", "fastapi", "express", "flask", "django", "endpoint", "graphql"],
        "agents": [
            {
                "id": "routes",
                "name": "API Routes & Controllers",
                "description": (
                    "Build all API routes and controllers. Implement every endpoint the user asked for "
                    "with proper HTTP methods, request validation, response formatting, "
                    "error handling, and pagination. Write complete working code."
                ),
                "files": ["routes/", "controllers/", "handlers/"],
            },
            {
                "id": "auth",
                "name": "Authentication & Middleware",
                "description": (
                    "Build auth system: JWT login/register/refresh, middleware for protected routes, "
                    "role-based permissions, password hashing, rate limiting, CORS, "
                    "request logging. Complete implementation."
                ),
                "files": ["middleware/", "auth/"],
            },
            {
                "id": "database",
                "name": "Database Layer",
                "description": (
                    "Build data layer: database models/schema (Prisma/SQLAlchemy/Mongoose), "
                    "migrations, repository pattern or ORM queries, seed data, "
                    "connection pooling config. Complete implementation."
                ),
                "files": ["models/", "database/", "prisma/", "migrations/"],
            },
        ],
        "integration_notes": "Server runs on port 4000. Use /api/v1 prefix for all routes.",
    },

    5: {
        "name": "ML / AI Engineering",
        "description": "ML system with data pipeline, model training, API, and UI",
        "keywords": ["machine learning", "ml", "ai", "deep learning", "neural network", "model", "train", "pytorch", "tensorflow", "sklearn", "transformers", "llm", "rag", "langchain", "openai", "ai engineer"],
        "agents": [
            {
                "id": "data",
                "name": "Data Pipeline",
                "description": (
                    "Build the complete data pipeline: data loading, cleaning, preprocessing, "
                    "feature engineering, train/test split, data validation. "
                    "Include requirements.txt with all deps. Write complete working code."
                ),
                "files": ["data/", "pipeline/", "preprocessing/"],
            },
            {
                "id": "model",
                "name": "Model & Training",
                "description": (
                    "Build the complete ML model: architecture definition, training loop, "
                    "evaluation metrics, hyperparameter config, model checkpointing, "
                    "inference code, model export. Write complete working code."
                ),
                "files": ["model/", "training/", "models/"],
            },
            {
                "id": "api",
                "name": "Inference API",
                "description": (
                    "Build the inference API with FastAPI: model loading, prediction endpoint, "
                    "input validation (pydantic), async inference, health check, "
                    "Docker support, requirements.txt. Port 8000. Complete working code."
                ),
                "files": ["api/", "serve/", "app.py"],
            },
            {
                "id": "ui",
                "name": "Demo UI",
                "description": (
                    "Build a Gradio or Streamlit demo UI: input fields for the model, "
                    "result display, examples, connected to the FastAPI backend. "
                    "Complete working code."
                ),
                "files": ["ui/", "demo/", "app_ui.py"],
            },
        ],
        "integration_notes": "API at port 8000. UI connects to http://localhost:8000. Model weights saved in models/.",
    },

    6: {
        "name": "Data Science",
        "description": "Data science project with EDA, feature engineering, modeling, and report",
        "keywords": ["data science", "data analysis", "eda", "exploratory data analysis", "pandas", "numpy", "matplotlib", "seaborn", "jupyter", "notebook", "regression", "classification", "clustering"],
        "agents": [
            {
                "id": "eda",
                "name": "EDA & Visualization",
                "description": (
                    "Build complete exploratory data analysis: data loading, summary statistics, "
                    "missing value analysis, distribution plots, correlation heatmap, "
                    "outlier detection, all with matplotlib/seaborn. Jupyter notebook + Python script."
                ),
                "files": ["notebooks/eda.ipynb", "eda.py", "visualizations/"],
            },
            {
                "id": "features",
                "name": "Feature Engineering",
                "description": (
                    "Build complete feature engineering pipeline: handling missing values, "
                    "encoding categoricals, scaling numerics, feature creation, "
                    "feature selection, saving processed data. Write complete working code."
                ),
                "files": ["features/", "preprocessing.py", "feature_engineering.py"],
            },
            {
                "id": "models",
                "name": "Modeling & Evaluation",
                "description": (
                    "Build complete ML modeling: multiple model comparison (baseline + 2-3 models), "
                    "cross-validation, hyperparameter tuning, evaluation metrics, "
                    "confusion matrix/ROC, model persistence. Write complete working code."
                ),
                "files": ["models/", "modeling.py", "evaluation.py"],
            },
            {
                "id": "report",
                "name": "Report & Dashboard",
                "description": (
                    "Build complete analysis report: Jupyter notebook with findings narrative, "
                    "key insights, visualizations, model comparison table, "
                    "recommendations section. Also create requirements.txt and README."
                ),
                "files": ["notebooks/report.ipynb", "README.md", "requirements.txt"],
            },
        ],
        "integration_notes": "Save processed data to data/processed/. Models to models/saved/.",
    },

    7: {
        "name": "Automation / Scripting",
        "description": "Automation tool with core logic, CLI, config, and tests",
        "keywords": ["automation", "script", "bot", "scraper", "crawler", "cli tool", "python script", "automate", "workflow", "task automation", "selenium", "playwright automation"],
        "agents": [
            {
                "id": "core",
                "name": "Core Logic",
                "description": (
                    "Build the core automation logic: main functions, data processing, "
                    "integration with external services/APIs, retry handling, "
                    "error recovery. Complete working implementation."
                ),
                "files": ["src/", "core/", "lib/"],
            },
            {
                "id": "cli",
                "name": "CLI Interface",
                "description": (
                    "Build the CLI with Click/Typer/argparse: all commands and options, "
                    "help text, input validation, progress bars (rich/tqdm), "
                    "colored output, config file loading. Complete working implementation."
                ),
                "files": ["cli.py", "main.py", "__main__.py"],
            },
            {
                "id": "config",
                "name": "Config & Setup",
                "description": (
                    "Build project infrastructure: requirements.txt with all deps, "
                    "setup.py or pyproject.toml, .env.example, config loader (pydantic settings), "
                    "logging setup, README with usage examples."
                ),
                "files": ["config.py", "settings.py", "requirements.txt", "setup.py", "pyproject.toml"],
            },
            {
                "id": "tests",
                "name": "Tests",
                "description": (
                    "Write comprehensive tests with pytest: unit tests for core logic, "
                    "integration tests, fixtures, mocks for external services, "
                    "test configuration. Aim for 80%+ coverage."
                ),
                "files": ["tests/"],
            },
        ],
        "integration_notes": "Entry point: python -m <package> or the CLI command. Tests: pytest tests/.",
    },

    8: {
        "name": "React / Next.js App",
        "description": "Modern React or Next.js app with components, pages, API, and styles",
        "keywords": ["react", "next.js", "nextjs", "react app", "react component", "tsx", "jsx", "vite react", "create react app", "react router"],
        "agents": [
            {
                "id": "components",
                "name": "UI Components",
                "description": (
                    "Build all reusable React components: buttons, cards, modals, forms, "
                    "inputs, layout components, navigation, with TypeScript interfaces. "
                    "Use Tailwind CSS. All components complete and functional."
                ),
                "files": ["src/components/", "components/"],
            },
            {
                "id": "pages",
                "name": "Pages & Routing",
                "description": (
                    "Build all pages/routes: implement every page the user asked for "
                    "with proper layout, data fetching (React Query/SWR/fetch), "
                    "loading states, error handling. Next.js App Router or React Router."
                ),
                "files": ["src/pages/", "src/app/", "app/"],
            },
            {
                "id": "state",
                "name": "State & API Layer",
                "description": (
                    "Build state management and API layer: Zustand/Redux store or Context, "
                    "API client functions, custom hooks, auth state, "
                    "TypeScript types for all API responses."
                ),
                "files": ["src/store/", "src/hooks/", "src/api/", "src/types/"],
            },
            {
                "id": "config",
                "name": "Config & Setup",
                "description": (
                    "Build project config: package.json with all dependencies, "
                    "Vite/Next.js config, Tailwind config, TypeScript config, "
                    "ESLint config, environment variables (.env.example), README."
                ),
                "files": ["package.json", "vite.config.*", "next.config.*", "tailwind.config.*", "tsconfig.json"],
            },
        ],
        "integration_notes": "Run: npm install && npm run dev. App on port 3000.",
    },

    9: {
        "name": "Python Package / Library",
        "description": "Publishable Python package with core code, CLI, tests, and docs",
        "keywords": ["python package", "library", "pip package", "pypi", "module", "python library", "sdk", "framework"],
        "agents": [
            {
                "id": "core",
                "name": "Core Library",
                "description": (
                    "Build the core library code: all public classes and functions, "
                    "type hints throughout, proper exception hierarchy, "
                    "__init__.py with clean public API. Complete working implementation."
                ),
                "files": ["src/<package>/", "<package>/"],
            },
            {
                "id": "cli",
                "name": "CLI & Entry Points",
                "description": (
                    "Build CLI with Click/Typer: all commands, options, help text. "
                    "Register entry_points in pyproject.toml. Complete working implementation."
                ),
                "files": ["src/<package>/cli.py", "cli.py"],
            },
            {
                "id": "tests",
                "name": "Tests",
                "description": (
                    "Write comprehensive pytest tests: unit tests for all public functions, "
                    "fixtures, parametrize where useful, edge cases, conftest.py. "
                    "80%+ coverage target."
                ),
                "files": ["tests/"],
            },
            {
                "id": "packaging",
                "name": "Packaging & Docs",
                "description": (
                    "Build packaging: pyproject.toml (name, version, deps, classifiers, entry_points), "
                    "README.md with installation and usage examples, CHANGELOG.md, "
                    ".github/workflows/publish.yml for PyPI publishing."
                ),
                "files": ["pyproject.toml", "README.md", "CHANGELOG.md", ".github/"],
            },
        ],
        "integration_notes": "Install: pip install -e .[dev]. Run tests: pytest. Publish: python -m build && twine upload dist/*.",
    },

    10: {
        "name": "DevOps / Infrastructure",
        "description": "Complete DevOps setup with Docker, CI/CD, Nginx, and monitoring",
        "keywords": ["devops", "docker", "kubernetes", "k8s", "ci/cd", "github actions", "nginx", "deployment", "infrastructure", "terraform", "ansible", "monitoring", "prometheus", "grafana"],
        "agents": [
            {
                "id": "containers",
                "name": "Docker & Compose",
                "description": (
                    "Build complete containerization: Dockerfile for each service (multi-stage builds), "
                    "docker-compose.yml (dev + prod), .dockerignore, environment variable handling, "
                    "health checks, volume mounts. Production-ready."
                ),
                "files": ["Dockerfile*", "docker-compose*.yml", ".dockerignore"],
            },
            {
                "id": "cicd",
                "name": "CI/CD Pipeline",
                "description": (
                    "Build complete CI/CD: GitHub Actions workflows (test.yml, deploy.yml), "
                    "automated testing on PR, Docker build and push to registry, "
                    "deployment to staging and production, secrets management."
                ),
                "files": [".github/workflows/"],
            },
            {
                "id": "nginx",
                "name": "Nginx & Proxy",
                "description": (
                    "Build Nginx configuration: reverse proxy for backend, "
                    "serve frontend static files, SSL/TLS config (Let's Encrypt), "
                    "rate limiting, gzip compression, security headers, "
                    "www redirect to non-www."
                ),
                "files": ["nginx/", "nginx.conf"],
            },
            {
                "id": "monitoring",
                "name": "Monitoring & Logging",
                "description": (
                    "Build monitoring setup: Prometheus scrape config, Grafana dashboards (JSON), "
                    "structured logging config, Loki log aggregation, "
                    "alert rules for down services and high error rates. "
                    "Add to docker-compose."
                ),
                "files": ["monitoring/", "prometheus.yml", "grafana/"],
            },
        ],
        "integration_notes": "docker-compose up -d to start. Services: app:3000, api:4000, nginx:80/443, prometheus:9090, grafana:3001.",
    },
}


# ---------------------------------------------------------------------------
# Preset matcher — LLM picks preset number, fast keyword fallback
# ---------------------------------------------------------------------------

_PRESET_SELECTOR_SYSTEM = """You are a build preset selector. Given a user request,
return the single best preset number (1-10) that matches what the user wants to build.

Presets:
1  = Website (HTML/CSS/JS, landing pages, portfolios, static sites)
2  = Full-Stack Web App (React/Vue + Express/FastAPI + database)
3  = SaaS Platform (full-stack + auth + Stripe payments + DevOps)
4  = REST API / Backend Service (API only, no frontend)
5  = ML/AI Engineering (model training + inference API + demo UI)
6  = Data Science (EDA + feature engineering + modeling + report)
7  = Automation / Scripting (CLI tools, bots, scrapers, task automation)
8  = React / Next.js App (React-focused frontend apps)
9  = Python Package / Library (installable pip package with tests)
10 = DevOps / Infrastructure (Docker, CI/CD, Nginx, monitoring)

Return ONLY the number. Nothing else."""


def _pick_preset_llm(user_input: str) -> Optional[int]:
    """Ask the LLM to pick a preset number. Fast: max_tokens=5."""
    try:
        from agent.llm import _get_client, PLANNER_MODEL
        client = _get_client()
        response = client.chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _PRESET_SELECTOR_SYSTEM},
                {"role": "user", "content": user_input[:500]},
            ],
            temperature=0,
            max_tokens=5,
        )
        text = (response.choices[0].message.content or "").strip()
        # Extract just the number
        m = re.search(r'\b(\d+)\b', text)
        if m:
            n = int(m.group(1))
            if 1 <= n <= len(PRESETS):
                return n
    except Exception:
        pass
    return None


def _pick_preset_keywords(user_input: str) -> Optional[int]:
    """Keyword fallback — score each preset against the user input."""
    text = user_input.lower()
    best_score = 0
    best_preset = None
    for pid, preset in PRESETS.items():
        score = sum(1 for kw in preset["keywords"] if kw in text)
        if score > best_score:
            best_score = score
            best_preset = pid
    return best_preset if best_score > 0 else None


def _pick_preset(user_input: str) -> Optional[int]:
    """Pick preset: LLM first (fast), keyword fallback."""
    pid = _pick_preset_llm(user_input)
    if pid:
        return pid
    return _pick_preset_keywords(user_input)


def _build_spec_from_preset(preset_id: int, user_input: str) -> ProjectSpec:
    """Build a ProjectSpec from a preset definition."""
    preset = PRESETS[preset_id]
    # Extract project name from user input (first 3-5 words after "build/create/make")
    name_match = re.search(
        r'\b(?:build|create|make|generate|write|develop|design)\s+(?:a\s+|an\s+)?(.+?)(?:\s+with|\s+using|\s+in|\s+that|$)',
        user_input, re.I
    )
    project_name = name_match.group(1).strip().title() if name_match else preset["name"]
    project_name = project_name[:40]  # cap length

    return ProjectSpec(
        project_name=project_name,
        description=f"{preset['description']} — {user_input[:100]}",
        tech_stack={},
        features=[
            FeatureSpec(
                id=a["id"],
                name=a["name"],
                description=a["description"],
                depends_on=a.get("depends_on", []),
                files=a.get("files", []),
            )
            for a in preset["agents"]
        ],
        integration_notes=preset.get("integration_notes", ""),
        preset_id=preset_id,
    )


# ---------------------------------------------------------------------------
# TaskProfile gate — only run parallel for complex, independent tasks
# ---------------------------------------------------------------------------

def should_run_parallel(profile: "object") -> bool:
    """Return True only when the TaskProfile warrants parallel execution.

    Accepts any object with .complexity, .category, .has_independent_subtasks
    so there is no hard import dependency on task_classifier at module level.
    """
    if getattr(profile, "complexity", None) != "complex":
        return False
    if getattr(profile, "category", None) == "web_automation":
        return False
    if not getattr(profile, "has_independent_subtasks", False):
        return False
    return True


# ---------------------------------------------------------------------------
# Quick reject — only bypass for obvious non-build requests
# ---------------------------------------------------------------------------

_SINGLE_AGENT_STARTERS = re.compile(
    r"^(fix|debug|explain|what|why|how|show|list|print|check|review|"
    r"refactor|rename|move|delete|remove|help|tell|"
    r"can you|could you|please|describe|summarize|read|open)\b",
    re.I,
)

_BUILD_TRIGGERS = re.compile(
    r"\b(build|create|make|generate|write|develop|design|scaffold|"
    r"implement|start|setup|set up|bootstrap|init)\b",
    re.I,
)


def _quick_reject(user_input: str) -> bool:
    """Return True only if this is definitely NOT a build request."""
    text = user_input.strip()
    if not text or text.startswith("/"):
        return True
    # Has a build verb → always evaluate
    if _BUILD_TRIGGERS.search(text):
        return False
    # Has preset keywords → evaluate
    for preset in PRESETS.values():
        for kw in preset["keywords"]:
            if kw in text.lower():
                return False
    # Short with no build intent
    if len(text.split()) < 6:
        return True
    if _SINGLE_AGENT_STARTERS.match(text):
        return True
    return False


# ---------------------------------------------------------------------------
# Agent prompt builder
# ---------------------------------------------------------------------------

def _build_feature_prompt(
    feature: FeatureSpec,
    spec: ProjectSpec,
    prior_outputs: Dict[str, str],
) -> str:
    preset_info = ""
    if spec.preset_id and spec.preset_id in PRESETS:
        preset_info = f"PRESET: {PRESETS[spec.preset_id]['name']}\n"

    lines = [
        f"PROJECT: {spec.project_name}",
        f"OVERALL GOAL: {spec.description}",
        preset_info,
        f"YOUR TASK: Build the '{feature.name}' component.",
        f"DESCRIPTION: {feature.description}",
        "",
    ]
    if feature.files:
        lines.append(f"FILES TO CREATE: {', '.join(feature.files)}")
        lines.append("")
    if spec.integration_notes:
        lines.append(f"INTEGRATION NOTES: {spec.integration_notes}")
        lines.append("")
    if prior_outputs:
        lines.append("ALREADY BUILT BY PARALLEL AGENTS:")
        for dep_id, summary in prior_outputs.items():
            if dep_id in feature.depends_on:
                lines.append(f"  [{dep_id}]: {summary[:400]}")
        lines.append("")

    # Auto-install instruction
    lines.append(
        "IMPORTANT: Before writing files, run the necessary install commands "
        "(npm install, pip install, etc.) via run_command if dependencies are needed.\n\n"
        "Build this component completely. Create ALL files. No placeholders, no TODOs, "
        "no stub functions. Write production-ready, working code.\n"
        "When done, reply with a concise summary: key file paths, ports, "
        "API routes, exported interfaces."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Progress dashboard
# ---------------------------------------------------------------------------

_STATUS_ICON  = {"pending": "·", "running": "⟳", "done": "✓", "failed": "✗"}
_STATUS_STYLE = {
    "pending": "dim",
    "running": "#E8FF3A bold",
    "done":    "#4ADE80 bold",
    "failed":  "#FF5A5A bold",
}
_BAR_CHARS = 20


class _Progress:
    def __init__(self, features: List[FeatureSpec]) -> None:
        import threading
        from agent.ui.console import console as _console
        self._statuses = {f.id: "pending" for f in features}
        self._names    = {f.id: f.name   for f in features}
        self._order    = [f.id for f in features]
        self._console  = _console
        self._live     = None
        self._lock     = threading.Lock()

    def _render(self):
        import time as _t
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich.console import Group

        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=3, no_wrap=True)
        grid.add_column(width=24, no_wrap=True)
        grid.add_column(width=10, no_wrap=True)
        grid.add_column(no_wrap=True)

        now = _t.monotonic()
        for fid in self._order:
            status = self._statuses.get(fid, "pending")
            name   = self._names.get(fid, fid)
            icon   = _STATUS_ICON.get(status, "·")
            style  = _STATUS_STYLE.get(status, "dim")
            filled = int((now % 5.0) / 5.0 * _BAR_CHARS) if status == "running" else (
                _BAR_CHARS if status == "done" else 0
            )
            bar = Text()
            bar.append("█" * filled, style=style)
            bar.append("░" * (_BAR_CHARS - filled), style="dim")
            grid.add_row(
                Text(icon, style=style),
                Text(name, style="bold white" if status == "running" else "white"),
                Text(status, style=style),
                bar,
            )

        done    = sum(1 for s in self._statuses.values() if s == "done")
        running = sum(1 for s in self._statuses.values() if s == "running")
        total   = len(self._statuses)
        summary = Text()
        summary.append(f" {done}/{total} done", style="#4ADE80")
        if running:
            summary.append(f"  ·  {running} running", style="#E8FF3A bold")

        return Panel(
            Group(grid, Text(""), summary),
            title=Text.assemble(("◈", "bold #E8FF3A"), (" Parallel Agents", "bold white")),
            title_align="left",
            border_style="#E8FF3A",
            padding=(1, 2),
            expand=False,
        )

    def start(self):
        from rich.live import Live
        with self._lock:
            self._live = Live(self._render(), console=self._console,
                              refresh_per_second=8, transient=False, auto_refresh=True)
            self._live.start()

    def stop(self):
        with self._lock:
            if self._live:
                self._live.update(self._render())
                self._live.stop()
                self._live = None

    def _refresh(self):
        if self._live:
            self._live.update(self._render())

    def set(self, fid, status):
        with self._lock:
            self._statuses[fid] = status
            self._refresh()

    def running(self, ids):
        with self._lock:
            for fid in ids:
                self._statuses[fid] = "running"
            self._refresh()

    def done(self, fid):
        with self._lock:
            self._statuses[fid] = "done"
            self._refresh()

    def failed(self, fid):
        with self._lock:
            self._statuses[fid] = "failed"
            self._refresh()


# ---------------------------------------------------------------------------
# DAG execution
# ---------------------------------------------------------------------------

def _topo_waves(features: List[FeatureSpec]) -> List[List[FeatureSpec]]:
    remaining = {f.id: f for f in features}
    completed: set = set()
    waves = []
    while remaining:
        ready = [f for f in remaining.values() if all(d in completed for d in f.depends_on)]
        if not ready:
            ready = list(remaining.values())
        waves.append(ready)
        for f in ready:
            completed.add(f.id)
            del remaining[f.id]
    return waves


def _run_feature_agent(feat: FeatureSpec, prompt: str, max_turns: int, skill_context: str) -> str:
    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    parent = get_session()
    nested = _build_nested(feat.name, prompt, parent.depth)
    if skill_context:
        nested.append({"role": "system", "content": skill_context})

    token = push_session(nested)
    ui.subagent_start(depth=nested.depth, description=feat.name)
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        ui.subagent_end(depth=nested.depth)
        return result.content or f"(built {feat.name})"
    except Exception as exc:
        return f"(error in {feat.name}: {exc})"
    finally:
        pop_session(token)


# ---------------------------------------------------------------------------
# Integration agent
# ---------------------------------------------------------------------------

def _build_integration_prompt(spec: ProjectSpec, outputs: Dict[str, str], original_request: str) -> str:
    preset_name = PRESETS.get(spec.preset_id, {}).get("name", "")
    lines = [
        f"INTEGRATION TASK for: {spec.project_name} ({preset_name})",
        f"ORIGINAL REQUEST: {original_request}",
    ]
    if spec.integration_notes:
        lines.append(f"INTEGRATION NOTES: {spec.integration_notes}")
    lines += ["", "OUTPUTS FROM PARALLEL AGENTS:"]
    for fid, output in outputs.items():
        lines.append(f"\n--- {fid} ---\n{output[:700]}")
    lines.append(
        "\n\nYour job:\n"
        "1. Verify all pieces connect correctly (imports, ports, env vars, CORS).\n"
        "2. Fix any integration gaps.\n"
        "3. Create missing glue files (root package.json, docker-compose.yml, .env.example).\n"
        "4. Add a README.md with run instructions.\n"
        "5. Run the project to verify it starts without errors.\n"
        "Summarize what was built and how to run it."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_parallel_build(
    user_input: str,
    skill_context: str = "",
    project_context: str = "",
    max_turns_per_agent: int = 60,
    on_progress: Optional[Callable[[str, str], None]] = None,
    profile: Optional[object] = None,
) -> Optional[str]:
    """Run parallel agents using preset-based architecture.

    Returns the final output string, or None if single-agent should handle it.

    If a TaskProfile is provided (from task_classifier.classify_task), it is
    checked first via should_run_parallel() before any LLM call is made.
    """
    # Profile gate — skip immediately if classifier says not parallel
    if profile is not None and not should_run_parallel(profile):
        return None

    if _quick_reject(user_input):
        return None

    # Pick preset
    ui.muted("  Selecting build preset...")
    preset_id = _pick_preset(user_input)
    if not preset_id:
        return None

    preset = PRESETS[preset_id]
    spec = _build_spec_from_preset(preset_id, user_input)

    n = len(spec.features)
    ui.info(
        f"  Preset {preset_id}: {preset['name']}  ·  "
        f"{n} parallel agent{'s' if n != 1 else ''}  ·  "
        f"{spec.project_name}"
    )

    progress = _Progress(spec.features)
    waves = _topo_waves(spec.features)
    outputs: Dict[str, str] = {}

    progress.start()
    set_parallel_mode(True)

    try:
        for wave in waves:
            progress.running([f.id for f in wave])

            if len(wave) == 1:
                feat = wave[0]
                prompt = _build_feature_prompt(feat, spec, outputs)
                result = _run_feature_agent(feat, prompt, max_turns_per_agent, skill_context)
                outputs[feat.id] = result
                progress.done(feat.id)
                if on_progress:
                    on_progress(feat.id, "done")
            else:
                def _worker(feat: FeatureSpec):
                    prompt = _build_feature_prompt(feat, spec, outputs)
                    result = _run_feature_agent(feat, prompt, max_turns_per_agent, skill_context)
                    return feat.id, result

                with ThreadPoolExecutor(max_workers=len(wave)) as pool:
                    futures = {pool.submit(_worker, f): f for f in wave}
                    for future in as_completed(futures):
                        feat = futures[future]
                        try:
                            fid, result = future.result()
                            outputs[fid] = result
                            progress.done(fid)
                            if on_progress:
                                on_progress(fid, "done")
                        except Exception as exc:
                            outputs[feat.id] = f"(failed: {exc})"
                            progress.failed(feat.id)

        # Add integrator to dashboard
        with progress._lock:
            progress._order.append("integrator")
            progress._statuses["integrator"] = "running"
            progress._names["integrator"] = "Integrator"
            progress._refresh()

    finally:
        set_parallel_mode(False)
        progress.stop()

    # Run integration
    ui.info("  Integrating...")
    int_feat = FeatureSpec(
        id="integrator",
        name="Integrator",
        description=_build_integration_prompt(spec, outputs, user_input),
    )
    final = _run_feature_agent(int_feat, int_feat.description, max_turns_per_agent, skill_context)
    return final
