"""Parallel multi-agent builder.

Turns a user request into a feature breakdown, then runs independent
feature agents (Frontend, Backend, Database, etc.) in parallel threads,
and integrates their outputs.

Architecture:
    User Request
         │
         ▼
    Planner Agent (spec + feature breakdown)
         │
    ┌────┼────┐
    ▼    ▼    ▼
   FE   BE   DB   (run in parallel)
    └────┼────┘
         ▼
    Integrator Agent
         ▼
    Final Result

Compared to sequential builds this is typically 3-8× faster for
multi-component projects (SaaS, ecommerce, full-stack apps).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

from agent import ui
from agent.env import getenv_int


MAX_PARALLEL_WORKERS = getenv_int("KRYTH_MAX_PARALLEL", 4)


# ---------------------------------------------------------------------------
# Project Spec
# ---------------------------------------------------------------------------

_SPEC_SYSTEM = """You are a senior software architect planning a parallel build.
Given a user request, output STRICT JSON (no markdown, no prose) with this shape:

{
  "project_name": "my-app",
  "description": "one-line description",
  "tech_stack": {"frontend": "React+TypeScript", "backend": "FastAPI", "database": "PostgreSQL"},
  "features": [
    {
      "id": "frontend",
      "name": "Frontend",
      "description": "what this agent should build",
      "depends_on": [],
      "files": ["src/App.tsx", "src/components/..."]
    },
    {
      "id": "backend",
      "name": "Backend API",
      "description": "what this agent should build",
      "depends_on": [],
      "files": ["app/main.py", "app/routers/..."]
    }
  ],
  "integration_notes": "how the pieces connect (ports, env vars, API contracts)"
}

Rules:
- features with empty depends_on can run in PARALLEL — keep depends_on minimal.
- Aim for 2-5 independent features for a typical project.
- Each feature description must be self-contained (include stack, style guide, etc.).
- integration_notes must have ports, env var names, and API contract summary."""


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

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectSpec":
        return cls(
            project_name=d.get("project_name", "project"),
            description=d.get("description", ""),
            tech_stack=d.get("tech_stack", {}),
            features=[
                FeatureSpec(
                    id=f.get("id", str(i)),
                    name=f.get("name", f"Feature {i}"),
                    description=f.get("description", ""),
                    depends_on=f.get("depends_on", []),
                    files=f.get("files", []),
                )
                for i, f in enumerate(d.get("features", []))
            ],
            integration_notes=d.get("integration_notes", ""),
        )


def _keyword_spec(user_input: str) -> Optional[ProjectSpec]:
    """Build a parallel feature spec from keywords — no LLM required.

    Covers the most common full-stack patterns so the parallel build
    always fires even when the planner model returns bad JSON.
    """
    text = user_input.lower()

    # Detect frontend stack
    if any(k in text for k in ["next.js", "nextjs"]):
        fe_stack, fe_name = "Next.js + TypeScript + Tailwind", "Next.js Frontend"
    elif any(k in text for k in ["vue", "nuxt"]):
        fe_stack, fe_name = "Vue 3 + TypeScript + Tailwind", "Vue Frontend"
    else:
        fe_stack, fe_name = "React + TypeScript + Vite + Tailwind", "React Frontend"

    # Detect backend stack
    if any(k in text for k in ["django"]):
        be_stack, be_name = "Django + DRF + JWT", "Django Backend"
    elif any(k in text for k in ["fastapi", "fast api"]):
        be_stack, be_name = "FastAPI + SQLAlchemy + JWT", "FastAPI Backend"
    elif any(k in text for k in ["express", "node"]):
        be_stack, be_name = "Express.js + TypeScript + JWT", "Node Backend"
    elif any(k in text for k in ["flask"]):
        be_stack, be_name = "Flask + SQLAlchemy + JWT", "Flask Backend"
    else:
        be_stack, be_name = "FastAPI + SQLAlchemy + JWT", "FastAPI Backend"

    # Detect DB
    if any(k in text for k in ["mongo", "mongodb"]):
        db_stack = "MongoDB + Mongoose"
    elif any(k in text for k in ["mysql"]):
        db_stack = "MySQL + SQLAlchemy"
    else:
        db_stack = "PostgreSQL + SQLAlchemy / Prisma"

    # Infer project name from first few words
    words = user_input.strip().split()
    name_words = [w for w in words if w.lower() not in
                  {"build","create","make","a","an","the","with","and","or","using"}]
    project_name = "-".join(name_words[:3]).lower() or "project"

    features = [
        FeatureSpec(
            id="frontend",
            name=fe_name,
            description=(
                f"Build the complete frontend using {fe_stack}. "
                f"Include: auth pages (login/register), dashboard, all main feature pages, "
                f"API integration with the backend at http://localhost:8000. "
                f"Use Tailwind CSS for styling. Make it production-quality with real data."
            ),
            depends_on=[],
            files=["frontend/src/", "frontend/package.json", "frontend/vite.config.ts"],
        ),
        FeatureSpec(
            id="backend",
            name=be_name,
            description=(
                f"Build the complete backend using {be_stack}. "
                f"Include: JWT authentication (register/login/refresh), all CRUD API endpoints, "
                f"CORS configured for http://localhost:5173, input validation, error handling. "
                f"Run on port 8000. Include requirements.txt or package.json."
            ),
            depends_on=[],
            files=["backend/", "backend/requirements.txt"],
        ),
        FeatureSpec(
            id="database",
            name="Database & Models",
            description=(
                f"Design and implement the database schema using {db_stack}. "
                f"Include: all models for the application, migrations, seed data, "
                f"indexes on commonly queried fields, relationships with cascade rules. "
                f"Export the schema as SQL or migration files."
            ),
            depends_on=[],
            files=["backend/models/", "backend/migrations/"],
        ),
    ]

    # Add auth feature for saas/crm/ecommerce
    if any(k in text for k in ["saas", "crm", "ecommerce", "e-commerce", "shop", "store", "platform"]):
        features.append(FeatureSpec(
            id="auth",
            name="Auth & Permissions",
            description=(
                "Implement complete authentication: JWT access + refresh tokens, "
                "role-based access control (admin/user), email verification flow, "
                "password reset, and rate limiting on auth endpoints."
            ),
            depends_on=[],
            files=["backend/auth/", "frontend/src/auth/"],
        ))

    return ProjectSpec(
        project_name=project_name,
        description=user_input,
        tech_stack={"frontend": fe_stack, "backend": be_stack, "database": db_stack},
        features=features,
        integration_notes=(
            f"Frontend runs on port 5173, backend on port 8000. "
            f"Backend must have CORS enabled for http://localhost:5173. "
            f"Frontend calls backend at http://localhost:8000/api/. "
            f"JWT tokens stored in localStorage. "
            f"Use .env files for secrets."
        ),
    )


def _create_spec(user_input: str, skill_context: str = "") -> Optional[ProjectSpec]:
    """Ask the planner to break the request into parallel features.

    Tries the LLM first for a smarter spec; falls back to keyword-based
    spec so the parallel build always fires for full-stack requests.
    """
    # Try LLM spec first
    try:
        from agent.llm import _get_client, PLANNER_MODEL
        client = _get_client()
        context = f"\n\nSkill context:\n{skill_context}" if skill_context else ""
        response = client.chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _SPEC_SYSTEM},
                {"role": "user", "content": user_input + context},
            ],
            temperature=0,
            max_tokens=1500,
        )
        text = (response.choices[0].message.content or "").strip()
        start = text.find("{")
        end   = text.rfind("}")
        if start >= 0 and end > start:
            d = json.loads(text[start:end + 1])
            spec = ProjectSpec.from_dict(d)
            if spec and len(spec.features) >= 2:
                return spec
    except Exception:
        pass

    # Fallback: keyword-based spec (always works, no LLM needed)
    ui.muted("  (using keyword spec fallback)")
    return _keyword_spec(user_input)


# ---------------------------------------------------------------------------
# DAG execution (topological, parallel)
# ---------------------------------------------------------------------------

def _topo_waves(features: List[FeatureSpec]) -> List[List[FeatureSpec]]:
    """Return features grouped into waves where each wave can run in parallel.

    Wave N only starts after all waves 0..N-1 have completed.
    """
    remaining = {f.id: f for f in features}
    completed: set[str] = set()
    waves: list[list[FeatureSpec]] = []

    while remaining:
        ready = [
            f for f in remaining.values()
            if all(dep in completed for dep in f.depends_on)
        ]
        if not ready:
            # Circular dep or unresolvable — add all remaining as one wave
            ready = list(remaining.values())
        waves.append(ready)
        for f in ready:
            completed.add(f.id)
            del remaining[f.id]

    return waves


# ---------------------------------------------------------------------------
# Feature agent prompt builder
# ---------------------------------------------------------------------------

def _build_feature_prompt(
    feature: FeatureSpec,
    spec: ProjectSpec,
    prior_outputs: Dict[str, str],
) -> str:
    lines = [
        f"PROJECT: {spec.project_name}",
        f"OVERALL GOAL: {spec.description}",
        f"TECH STACK: {json.dumps(spec.tech_stack)}",
        "",
        f"YOUR TASK: Build the '{feature.name}' feature.",
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
        lines.append("ALREADY BUILT BY PARALLEL AGENTS (use these interfaces):")
        for dep_id, summary in prior_outputs.items():
            if dep_id in feature.depends_on:
                lines.append(f"  [{dep_id}]: {summary[:400]}")
        lines.append("")
    lines.append(
        "Build this feature completely. Create ALL files. Wire real behavior. "
        "No placeholders or TODOs. When done, reply with a one-paragraph summary "
        "of what you built and the key file paths and interfaces (ports, API routes, "
        "exported functions) so the integrator can wire everything together."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Live parallel progress dashboard
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
    """Rich Live dashboard — shows all agents in a single updating panel.

    While agents run in parallel threads, this panel refreshes in-place
    every ~100 ms so the user sees real-time status for every agent
    simultaneously instead of scattered scrolling lines.
    """

    def __init__(self, features: List[FeatureSpec]) -> None:
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from agent.ui.console import console as _console

        self._statuses: Dict[str, str] = {f.id: "pending" for f in features}
        self._names:    Dict[str, str]  = {f.id: f.name   for f in features}
        self._order:    List[str]       = [f.id for f in features]
        self._console = _console
        self._live: Live | None = None

    # ── Build the renderable ──────────────────────────────────────────────

    def _render(self) -> "Panel":
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=3,  no_wrap=True)   # icon
        grid.add_column(width=22, no_wrap=True)   # name
        grid.add_column(width=12, no_wrap=True)   # status label
        grid.add_column(no_wrap=True)              # bar

        for fid in self._order:
            status = self._statuses.get(fid, "pending")
            name   = self._names.get(fid, fid)
            icon   = _STATUS_ICON.get(status, "·")
            style  = _STATUS_STYLE.get(status, "dim")

            # Animated bar: fills left-to-right while running, full when done
            if status == "running":
                import time
                filled = int((time.monotonic() * 4) % (_BAR_CHARS + 1))
            elif status == "done":
                filled = _BAR_CHARS
            else:
                filled = 0

            bar = Text()
            bar.append("█" * filled,          style=style)
            bar.append("░" * (_BAR_CHARS - filled), style="dim")

            icon_text   = Text(icon,    style=style)
            name_text   = Text(name,    style="bold white" if status == "running" else "white")
            status_text = Text(status,  style=style)

            grid.add_row(icon_text, name_text, status_text, bar)

        # Count summary
        done    = sum(1 for s in self._statuses.values() if s == "done")
        running = sum(1 for s in self._statuses.values() if s == "running")
        total   = len(self._statuses)
        summary = Text()
        summary.append(f" {done}/{total} done", style="#4ADE80")
        if running:
            summary.append(f"  ·  {running} running", style="#E8FF3A")

        from rich.console import Group
        from rich.text import Text as T
        body = Group(grid, T(""), summary)

        return Panel(
            body,
            title=Text.assemble(("◈", "bold #E8FF3A"), (" Parallel Agents", "bold white")),
            title_align="left",
            border_style="#E8FF3A",
            padding=(1, 2),
            expand=False,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        from rich.live import Live
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=10,
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            self._live.update(self._render())
            self._live.stop()
            self._live = None

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    # ── Public API ────────────────────────────────────────────────────────

    def set(self, feature_id: str, status: str) -> None:
        self._statuses[feature_id] = status
        self._refresh()

    def running(self, ids: List[str]) -> None:
        for fid in ids:
            self._statuses[fid] = "running"
        self._refresh()

    def done(self, fid: str) -> None:
        self._statuses[fid] = "done"
        self._refresh()

    def failed(self, fid: str) -> None:
        self._statuses[fid] = "failed"
        self._refresh()


# ---------------------------------------------------------------------------
# Main parallel build entry point
# ---------------------------------------------------------------------------

def run_parallel_build(
    user_input: str,
    skill_context: str = "",
    max_turns_per_agent: int = 40,
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> Optional[str]:
    """Run a parallel multi-agent build for a complex request.

    Returns the final integrated output, or None if the request doesn't
    warrant parallel execution (simple / single-feature requests).

    Parameters
    ----------
    user_input:
        The user's request.
    skill_context:
        Composed skill prompts to inject into each feature agent.
    max_turns_per_agent:
        How many tool-call turns each feature agent gets.
    on_progress:
        Optional callback(feature_id, status) for live updates.
    """
    # Only trigger for complex build requests
    if not _is_complex_build(user_input):
        return None

    ui.info("Planning parallel build...")
    spec = _create_spec(user_input, skill_context)
    if not spec or len(spec.features) < 2:
        return None  # Not multi-feature; let the normal loop handle it

    ui.info(
        f"  Project: {spec.project_name}  ·  "
        f"{len(spec.features)} features  ·  "
        f"stack: {', '.join(spec.tech_stack.values())}"
    )

    progress = _Progress(spec.features)
    waves = _topo_waves(spec.features)
    outputs: Dict[str, str] = {}

    # Start the live dashboard — stays on screen throughout the build
    progress.start()

    try:
        for wave_idx, wave in enumerate(waves):
            progress.running([f.id for f in wave])

            if len(wave) == 1:
                feat = wave[0]
                prompt = _build_feature_prompt(feat, spec, outputs)
                result = _run_feature_agent(feat, prompt, max_turns_per_agent, skill_context)
                outputs[feat.id] = result
                if on_progress:
                    on_progress(feat.id, "done")
                progress.done(feat.id)
            else:
                # Multiple independent features — run in parallel threads
                def _worker(feat: FeatureSpec) -> tuple[str, str]:
                    prompt = _build_feature_prompt(feat, spec, outputs)
                    result = _run_feature_agent(feat, prompt, max_turns_per_agent, skill_context)
                    return feat.id, result

                workers = min(len(wave), MAX_PARALLEL_WORKERS)
                with ThreadPoolExecutor(max_workers=workers) as pool:
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
        integrator_feat = FeatureSpec(id="integrator", name="Integrator", description="")
        progress._order.append("integrator")
        progress._statuses["integrator"] = "running"
        progress._names["integrator"] = "Integrator"
        progress._refresh()

    finally:
        # Stop live panel before printing anything else
        progress.stop()

    ui.info("  Integrating...")
    final = _run_integrator(spec, outputs, user_input, skill_context, max_turns_per_agent)
    return final


def _run_feature_agent(
    feat: FeatureSpec,
    prompt: str,
    max_turns: int,
    skill_context: str,
) -> str:
    """Run a single feature agent in its own isolated session."""
    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    parent = get_session()
    system_extra = skill_context if skill_context else ""
    nested = _build_nested(feat.name, prompt, parent.depth)
    if system_extra:
        nested.append({"role": "system", "content": system_extra})

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


def _run_integrator(
    spec: ProjectSpec,
    outputs: Dict[str, str],
    original_request: str,
    skill_context: str,
    max_turns: int,
) -> str:
    """Run the integrator agent that wires all feature outputs together."""
    summary_lines = [
        f"INTEGRATION TASK for: {spec.project_name}",
        f"ORIGINAL REQUEST: {original_request}",
        f"INTEGRATION NOTES: {spec.integration_notes}",
        "",
        "OUTPUTS FROM PARALLEL AGENTS:",
    ]
    for fid, output in outputs.items():
        summary_lines.append(f"\n--- {fid} ---\n{output[:600]}")
    summary_lines.append(
        "\n\nYour job:\n"
        "1. Verify all pieces connect correctly (imports, ports, env vars).\n"
        "2. Fix any integration gaps (missing CORS headers, wrong API URLs, etc.).\n"
        "3. Create any missing glue files (root package.json, docker-compose.yml, .env.example).\n"
        "4. Add a README.md with run instructions.\n"
        "5. Run the project to verify it starts correctly.\n"
        "When done, summarize what was built and how to run it."
    )
    prompt = "\n".join(summary_lines)

    from agent.tools._subagent import _build_nested
    from agent.agent_loop import run_inner_loop
    from agent.session import push_session, pop_session, get_session

    parent = get_session()
    nested = _build_nested("Integrator", prompt, parent.depth)
    if skill_context:
        nested.append({"role": "system", "content": skill_context})

    token = push_session(nested)
    ui.subagent_start(depth=nested.depth, description="Integrator")
    try:
        result = run_inner_loop(nested, max_turns, verbose_usage=False)
        ui.subagent_end(depth=nested.depth)
        return result.content or "Integration complete."
    except Exception as exc:
        return f"Integration error: {exc}"
    finally:
        pop_session(token)


# ---------------------------------------------------------------------------
# Heuristic: is this a complex multi-component build?
# ---------------------------------------------------------------------------

_COMPLEX_PATTERNS = re.compile(
    r"\b(saas|ecommerce|e-commerce|full.?stack|fullstack|dashboard|platform|"
    r"crm|marketplace|cms|shop|store|app with auth|app with payment|"
    r"complete|production.ready|entire|whole)\b",
    re.I,
)
_SIMPLE_PATTERNS = re.compile(
    r"\b(fix|debug|refactor|rename|update|add|remove|change|explain|review)\b",
    re.I,
)


def _is_complex_build(user_input: str) -> bool:
    """True if the request is a complex multi-component build."""
    if _SIMPLE_PATTERNS.search(user_input):
        return False
    return bool(_COMPLEX_PATTERNS.search(user_input))
