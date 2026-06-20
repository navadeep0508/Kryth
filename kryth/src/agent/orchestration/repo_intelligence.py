"""Repository Intelligence — scans the repository and returns a structured
profile describing languages, frameworks, architecture, and existing services.

Never uses hardcoded assumptions. Everything is derived from actual files.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class RepoProfile:
    """Complete repository profile derived from static analysis."""
    root: str
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    architecture: List[str] = field(default_factory=list)      # e.g. monorepo, microservices
    has_auth: bool = False
    has_payments: bool = False
    has_database: bool = False
    has_docker: bool = False
    has_cicd: bool = False
    has_tests: bool = False
    has_frontend: bool = False
    has_backend: bool = False
    has_api: bool = False
    existing_services: List[str] = field(default_factory=list)
    dependency_files: List[str] = field(default_factory=list)  # package.json, requirements.txt ...
    entry_points: List[str] = field(default_factory=list)
    test_frameworks: List[str] = field(default_factory=list)
    code_style: Dict[str, str] = field(default_factory=dict)   # indent, quote_style ...
    existing_apis: List[str] = field(default_factory=list)
    file_count: int = 0
    is_empty: bool = True


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

_LANG_EXTS: Dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "JavaScript", ".tsx": "TypeScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".cs": "C#",
    ".cpp": "C++", ".c": "C", ".swift": "Swift",
    ".dart": "Dart", ".vue": "Vue", ".svelte": "Svelte",
    ".ex": "Elixir", ".exs": "Elixir", ".clj": "Clojure",
}

_FRAMEWORK_PATTERNS: List[tuple[str, str]] = [
    (r"react", "React"), (r"next[.\-]?js|\"next\"", "Next.js"),
    (r"vue", "Vue"), (r"angular", "Angular"), (r"svelte", "Svelte"),
    (r"nuxt", "Nuxt"), (r"gatsby", "Gatsby"), (r"remix", "Remix"),
    (r"fastapi", "FastAPI"), (r"django", "Django"), (r"flask", "Flask"),
    (r"express", "Express"), (r"nest(?:js)?", "NestJS"), (r"hono", "Hono"),
    (r"rails|ruby.on.rails", "Rails"), (r"spring", "Spring"),
    (r"laravel", "Laravel"), (r"gin\b", "Gin"), (r"fiber\b", "Fiber"),
    (r"sqlalchemy", "SQLAlchemy"), (r"prisma", "Prisma"),
    (r"mongoose", "Mongoose"), (r"typeorm", "TypeORM"),
    (r"pytest", "pytest"), (r"jest\b", "Jest"), (r"vitest", "Vitest"),
    (r"tailwind", "Tailwind CSS"), (r"shadcn", "shadcn/ui"),
    (r"chakra", "Chakra UI"), (r"material.ui|@mui", "Material UI"),
]

_AUTH_PATTERNS = re.compile(
    r"passport|jwt|auth0|clerk|supabase.auth|firebase.auth|"
    r"nextauth|lucia|better.auth|oauth|bcrypt|argon2|"
    r"authentication|authorize|login_required|@login", re.I
)
_PAYMENT_PATTERNS = re.compile(
    r"stripe|braintree|paypal|square|paddle|lemonsqueezy|"
    r"billing|subscription|checkout|webhook.*payment", re.I
)
_DB_PATTERNS = re.compile(
    r"postgres|postgresql|mysql|sqlite|mongodb|redis|"
    r"supabase|planetscale|neon|turso|firestore|dynamodb|"
    r"database_url|db\.connect|createConnection|pool\.", re.I
)
_API_ROUTE_PATTERNS = re.compile(
    r'@app\.(get|post|put|delete|patch)\s*\(|'
    r'router\.(get|post|put|delete|patch)\s*\(|'
    r'app\.(get|post|put|delete|patch)\s*\(|'
    r"Route\(|@Controller|@Get\(|@Post\(", re.I
)

_IGNORE = {
    "node_modules", ".git", "__pycache__", "venv", ".venv",
    "dist", "build", ".next", ".cache", "coverage",
}


def _walk(root: str):
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _IGNORE and not d.startswith(".")]
        for f in files:
            yield os.path.join(dirpath, f)


def _read_head(path: str, chars: int = 8000) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(chars)
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_repo(root: str = ".") -> RepoProfile:
    """Scan the repository and return a RepoProfile."""
    root = os.path.abspath(root)
    profile = RepoProfile(root=root)

    lang_counts: Dict[str, int] = {}
    framework_hits: Set[str] = set()
    has_auth = has_payments = has_db = False
    test_dirs: Set[str] = set()
    file_count = 0

    for path in _walk(root):
        file_count += 1
        name = os.path.basename(path)
        _, ext = os.path.splitext(name)
        rel = os.path.relpath(path, root).replace("\\", "/")

        # Languages
        lang = _LANG_EXTS.get(ext.lower())
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

        # Dependency / config files
        dep_names = {
            "package.json", "requirements.txt", "pyproject.toml",
            "Pipfile", "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
            "composer.json", "Gemfile",
        }
        if name in dep_names:
            profile.dependency_files.append(rel)

        # Docker
        if name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                    "compose.yml", "compose.yaml"):
            profile.has_docker = True

        # CI/CD
        if ".github/workflows" in rel or "gitlab-ci" in name or \
                "circle" in name.lower() or "jenkins" in name.lower():
            profile.has_cicd = True

        # Entry points
        if name in ("main.py", "app.py", "server.py", "index.js",
                    "index.ts", "main.go", "main.rs", "app.ts"):
            profile.entry_points.append(rel)

        # Tests
        if "test" in rel.lower() or "spec" in rel.lower():
            test_dirs.add(os.path.dirname(rel))
            profile.has_tests = True

        # Frontend / backend directories
        parts = set(rel.lower().split("/"))
        if parts & {"frontend", "client", "web", "ui", "app", "pages", "components"}:
            profile.has_frontend = True
        if parts & {"backend", "server", "api", "services", "src"}:
            profile.has_backend = True

        # Read file content for deeper signals
        if ext.lower() in _LANG_EXTS or name.endswith(".json"):
            content = _read_head(path, 6000)
            if not content:
                continue

            # Frameworks
            for pattern, fw_name in _FRAMEWORK_PATTERNS:
                if re.search(pattern, content, re.I) and fw_name not in framework_hits:
                    framework_hits.add(fw_name)

            has_auth = has_auth or bool(_AUTH_PATTERNS.search(content))
            has_payments = has_payments or bool(_PAYMENT_PATTERNS.search(content))
            has_db = has_db or bool(_DB_PATTERNS.search(content))

            # API routes
            for m in _API_ROUTE_PATTERNS.finditer(content):
                route_context = content[max(0, m.start()-20):m.end()+60].strip()
                if route_context not in profile.existing_apis:
                    profile.existing_apis.append(route_context[:100])

    profile.file_count = file_count
    profile.is_empty = file_count == 0

    # Finalize languages (top-5 by count)
    profile.languages = [
        lang for lang, _ in sorted(lang_counts.items(), key=lambda x: -x[1])
    ][:5]

    profile.frameworks = sorted(framework_hits)
    profile.has_auth = has_auth
    profile.has_payments = has_payments
    profile.has_database = has_db
    profile.has_api = bool(profile.existing_apis)

    # Architecture
    arch: List[str] = []
    root_path = Path(root)
    # Monorepo: multiple package.json or multiple pyproject.toml
    pkg_jsons = list(root_path.rglob("package.json"))
    pkg_jsons = [p for p in pkg_jsons if _IGNORE.isdisjoint(set(p.parts))]
    if len(pkg_jsons) > 2:
        arch.append("monorepo")
    if profile.has_frontend and profile.has_backend:
        arch.append("fullstack")
    if profile.has_docker:
        arch.append("containerised")
    if profile.has_tests:
        arch.append("tested")
    profile.architecture = arch

    # Test frameworks
    fw_lower = {f.lower() for f in framework_hits}
    for tf in ("pytest", "Jest", "Vitest", "unittest"):
        if tf.lower() in fw_lower:
            profile.test_frameworks.append(tf)

    return profile
