"""Persistent worker pool — early agent spawning before DAG is complete (Rule 15).

Architecture:
  1. Prompt arrives → spawn_early_for_prompt() fires immediately
  2. Workers begin repository reads based on keyword heuristics
  3. Meanwhile orchestration builds the DAG and team
  4. When real agent tasks are dispatched, workers already hold pre-loaded context
  5. ZeroIdleScheduler assigns read/validate tasks to idle workers between turns

Worker lifecycle:
  warm_up()  → read repo files matching domain patterns
  submit()   → run a real agent task
  shutdown() → clean up executors
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

# ── Domain keyword detection ──────────────────────────────────────────────────

DOMAIN_KEYWORDS: dict[str, str] = {
    # Frontend
    "frontend": "frontend", "react": "frontend", "vue": "frontend",
    "angular": "frontend", "svelte": "frontend", "next": "frontend",
    "nuxt": "frontend", "css": "frontend", "tailwind": "frontend",
    "component": "frontend", "ui": "frontend", "landing": "frontend",
    "dashboard": "frontend", "page": "frontend",
    # Backend
    "backend": "backend", "api": "backend", "server": "backend",
    "fastapi": "backend", "express": "backend", "django": "backend",
    "flask": "backend", "nest": "backend", "rails": "backend",
    "auth": "backend", "jwt": "backend", "middleware": "backend",
    "endpoint": "backend", "route": "backend",
    # Database
    "database": "database", "schema": "database", "migration": "database",
    "model": "database", "orm": "database", "prisma": "database",
    "postgres": "database", "mysql": "database", "mongo": "database",
    "redis": "database", "sqlite": "database",
    # Testing
    "test": "testing", "pytest": "testing", "jest": "testing",
    "spec": "testing", "coverage": "testing", "e2e": "testing",
    "vitest": "testing",
    # Browser
    "browser": "browser", "playwright": "browser", "selenium": "browser",
    "scrape": "browser", "crawl": "browser", "automate": "browser",
    # Refactor
    "refactor": "refactor", "clean": "refactor", "optimize": "refactor",
    "simplify": "refactor", "restructure": "refactor",
}

# Files to pre-load per domain
DOMAIN_READ_PATTERNS: dict[str, list[str]] = {
    "frontend":  ["components", "pages", "routes", "styles", "hooks",
                  "App", "Layout", "index"],
    "backend":   ["routes", "handlers", "controllers", "api", "middleware",
                  "auth", "services", "utils"],
    "database":  ["models", "schemas", "migrations", "db", "orm",
                  "seed", "repository"],
    "testing":   ["tests", "test_", "spec", "fixtures", "conftest",
                  "mock", "factory"],
    "browser":   ["playwright", "selenium", "browser", "e2e", "page"],
    "refactor":  [],
    "recovery":  [],
    "research":  [],
}

_IGNORE_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".turbo", "coverage", ".pytest_cache",
    ".mypy_cache", "htmlcov",
})


# ── Worker state ──────────────────────────────────────────────────────────────

@dataclass
class DomainWorker:
    domain: str
    preloaded_files: list[str] = field(default_factory=list)
    warm: bool = False
    idle: bool = True
    current_task: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_busy(self, task: str) -> None:
        with self._lock:
            self.idle = False
            self.current_task = task

    def set_idle(self) -> None:
        with self._lock:
            self.idle = True
            self.current_task = ""


# ── Worker pool ───────────────────────────────────────────────────────────────

class WorkerPool:
    """Persistent per-domain thread pools.

    Workers warm up immediately with repo scans. Real agent tasks are submitted
    to the same pools, so threads are already hot (no cold-start overhead).
    """

    def __init__(self) -> None:
        self._pools: dict[str, ThreadPoolExecutor] = {}
        self._workers: dict[str, DomainWorker] = {}
        self._lock = threading.Lock()
        self._active = True

    def _pool_for(self, domain: str) -> ThreadPoolExecutor:
        with self._lock:
            if domain not in self._pools:
                # 2 threads for heavy domains, 1 for others
                n = 2 if domain in ("frontend", "backend", "testing") else 1
                self._pools[domain] = ThreadPoolExecutor(
                    max_workers=n,
                    thread_name_prefix=f"kryth-{domain}",
                )
                self._workers[domain] = DomainWorker(domain=domain)
        return self._pools[domain]

    def warm_up(self, domain: str, patterns: list[str]) -> Future:
        """Submit a repo scan to the domain pool — runs in background."""
        pool = self._pool_for(domain)
        worker = self._workers[domain]

        def _scan() -> list[str]:
            worker.set_busy("repo-scan")
            found: list[str] = []
            try:
                if not patterns:
                    return found
                for dirpath, dirnames, filenames in os.walk("."):
                    dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
                    for fname in filenames:
                        fpath = os.path.join(dirpath, fname)
                        if any(p.lower() in fname.lower() or p.lower() in fpath.lower()
                               for p in patterns):
                            try:
                                if os.path.getsize(fpath) < 40_000:
                                    found.append(fpath)
                            except OSError:
                                pass
                    if len(found) >= 15:
                        break
            finally:
                worker.preloaded_files = found[:15]
                worker.warm = True
                worker.set_idle()
            return worker.preloaded_files

        return pool.submit(_scan)

    def submit(self, domain: str, fn: Callable, *args, **kwargs) -> Future:
        """Submit real agent work to the domain pool."""
        pool = self._pool_for(domain)
        worker = self._workers.get(domain)

        def _wrapped():
            if worker:
                worker.set_busy(getattr(fn, "__name__", "task"))
            try:
                return fn(*args, **kwargs)
            finally:
                if worker:
                    worker.set_idle()

        return pool.submit(_wrapped)

    def idle_workers(self) -> list[DomainWorker]:
        """Return list of workers that are currently idle."""
        with self._lock:
            return [w for w in self._workers.values() if w.idle]

    def assign_zero_idle_task(self, domain: str, fn: Callable, *args, **kwargs) -> Future | None:
        """Rule 20: if a worker is idle, assign it a task. No-op if busy."""
        worker = self._workers.get(domain)
        if worker and worker.idle:
            return self.submit(domain, fn, *args, **kwargs)
        return None

    def status(self) -> dict:
        with self._lock:
            return {
                domain: {
                    "warm": w.warm,
                    "idle": w.idle,
                    "task": w.current_task,
                    "preloaded": len(w.preloaded_files),
                }
                for domain, w in self._workers.items()
            }

    def shutdown(self, wait: bool = False) -> None:
        self._active = False
        with self._lock:
            for pool in self._pools.values():
                pool.shutdown(wait=wait, cancel_futures=True)
            self._pools.clear()
            self._workers.clear()


# ── Singleton ─────────────────────────────────────────────────────────────────

_pool: WorkerPool | None = None
_pool_lock = threading.Lock()


def get_worker_pool() -> WorkerPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = WorkerPool()
    return _pool


# ── Early spawn entry point ───────────────────────────────────────────────────

def spawn_early_for_prompt(user_input: str) -> dict[str, Future]:
    """Detect domains from prompt and warm-up workers immediately.

    Called as the FIRST thing when a prompt arrives — fires before any LLM call.
    Returns dict[domain → Future[list[str]]] for optional awaiting.

    Workers begin reading repository files while the orchestration pipeline
    builds the DAG. By the time real tasks are dispatched, workers already
    hold pre-loaded context for their domain.
    """
    lower = user_input.lower()

    domains: set[str] = set()
    for keyword, domain in DOMAIN_KEYWORDS.items():
        if keyword in lower:
            domains.add(domain)

    # Every complex task benefits from a backend warmup as baseline
    domains.add("backend")

    pool = get_worker_pool()
    futures: dict[str, Future] = {}
    for domain in domains:
        patterns = DOMAIN_READ_PATTERNS.get(domain, [])
        futures[domain] = pool.warm_up(domain, patterns)

    return futures


# ── Lead agent helpers ────────────────────────────────────────────────────────

def create_lead_prompt(user_input: str, domains: list[str]) -> str:
    """Generate a lead-agent coordination prompt from detected domains."""
    domain_list = ", ".join(domains) if domains else "general"
    return (
        f"You are the Lead Agent for this task. "
        f"Detected domains: {domain_list}. "
        f"Task: {user_input}\n\n"
        "Your job:\n"
        "1. Create a rough file-level plan immediately (no more than 10 lines)\n"
        "2. List the exact files each domain worker should create\n"
        "3. Identify shared dependencies to resolve first\n"
        "4. Keep the plan minimal — workers will fill in details\n\n"
        "Output format:\n"
        "PLAN: <one sentence>\n"
        "FILES:\n"
        "  frontend: [file1, file2, ...]\n"
        "  backend: [file1, file2, ...]\n"
        "SHARED: [shared modules, configs, types]\n"
    )


def inject_preloaded_context(session, futures: dict[str, Future], timeout: float = 0.5) -> None:
    """Wait briefly for warmup futures and inject preloaded file lists into session."""
    preloaded: list[str] = []
    for domain, fut in futures.items():
        try:
            files = fut.result(timeout=timeout)
            if files:
                preloaded.extend(files)
        except Exception:
            pass

    if preloaded:
        unique = list(dict.fromkeys(preloaded))[:20]  # dedupe, cap at 20
        try:
            session.append({
                "role": "system",
                "content": f"[Worker Pool: preloaded {len(unique)} relevant files]\n"
                           + "\n".join(unique),
            })
        except Exception:
            pass
