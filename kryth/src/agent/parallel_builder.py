"""Parallel multi-agent builder.

The LLM decides EVERYTHING:
  - Whether parallel agents are even needed (returns None → normal loop runs)
  - Exactly which agents to create (minimum needed, no forced set)
  - What integration work is required after the build

No keyword heuristics. No forced agent counts. No fallback spec that invents
agents that weren't asked for.

Flow:
    User Request + Project Context
         │
         ▼
    LLM: do we need parallel agents? which ones?
         │
    ┌────┴─────────────────────────────┐
    │ no / 1 agent                     │ 2+ independent agents
    ▼                                  ▼
 return None                   run exactly those agents
 (normal loop handles it)      in parallel per DAG waves
                                       │
                                       ▼
                               LLM: what integration is needed?
                               (0-N tasks, scaled to complexity)
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable

from agent import ui
from agent.tools._subagent import spawn_agents_parallel
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
        )


# ---------------------------------------------------------------------------
# LLM agent planner — single call that decides everything
# ---------------------------------------------------------------------------

_PLANNER_SYSTEM = """You are a build planner for an AI coding agent.

Given the user request and current project context, decide whether the task needs
MULTIPLE INDEPENDENT agents working in parallel, and if so — exactly which ones.

Return STRICT JSON only (no markdown fence, no prose):

{
  "needs_parallel": false,
  "reason": "one sentence explanation",
  "project_name": "my-app",
  "description": "one-line description",
  "tech_stack": {"frontend": "SASS+JS", "backend": "Node"},
  "agents": [],
  "integration_notes": ""
}

When needs_parallel is true, populate agents:
[
  {
    "id": "structure",
    "name": "HTML Structure",
    "description": "Complete self-contained task — include all sections, semantic HTML, real content",
    "depends_on": [],
    "files": ["index.html"]
  }
]

RULES — read carefully:
- needs_parallel = false for: bug fixes, refactoring, explanations, code review,
  adding a single feature to existing code, small scripts.
- needs_parallel = true for ANY of these:
  * Building a website, landing page, dashboard, or web app (split into:
    HTML structure agent + SCSS/styles agent + JS/interactivity agent)
  * Full-stack apps with separate frontend and backend
  * Projects with 3+ independent file groups that can be written simultaneously
  * Any request with words: website, app, dashboard, landing, saas, sass, scss,
    react, vue, next, api, fullstack, full-stack, modern, complete, all working
- For website/SASS/CSS builds ALWAYS use 3 agents:
  1. "html" agent → writes all HTML files with real content and structure
  2. "styles" agent → writes all SCSS/CSS files with real design, animations, responsive
  3. "scripts" agent → writes all JS files with real interactivity
- Each agent owns a completely separate file type — they never conflict.
- Use the MINIMUM agents needed. For a simple website: 3 (html, styles, scripts).
- Do NOT include an integration agent — integration is handled separately.
- agents must be [] when needs_parallel is false.
"""


def _llm_plan_agents(user_input: str, project_context: str = "") -> Optional[ProjectSpec]:
    """Ask the LLM to decide if parallel agents are needed and produce the spec.

    Returns None  → caller should use the normal single-agent loop.
    Returns spec  → caller should run exactly those agents in parallel.
    """
    try:
        from agent.llm import _get_client, PLANNER_MODEL
        client = _get_client()

        ctx_section = ""
        if project_context:
            ctx_section = f"\n\nCurrent project context (first 2000 chars):\n{project_context[:2000]}"

        response = client.chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user", "content": user_input + ctx_section},
            ],
            temperature=0,
            max_tokens=2000,
        )
        text = (response.choices[0].message.content or "").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None

        d = json.loads(text[start:end + 1])

        if not d.get("needs_parallel"):
            return None  # LLM explicitly says single agent is fine

        agents = d.get("agents") or []
        if len(agents) < 2:
            # LLM said parallel but gave < 2 agents — treat as single-agent
            return None

        return ProjectSpec.from_dict(d)

    except Exception:
        return None  # Any failure → fall through to normal loop (never force agents)


# ---------------------------------------------------------------------------
# Lightweight pre-filter — skip obvious single-agent requests before LLM call
# ---------------------------------------------------------------------------

# These opening words almost always signal single-agent work.
_SINGLE_AGENT_STARTERS = re.compile(
    r"^(fix|debug|explain|what|why|how|show|list|print|check|review|"
    r"refactor|rename|move|delete|remove|update|change|add|help|tell|"
    r"can you|could you|please|describe|summarize|read|open|run|test)\b",
    re.I,
)

# Keywords that always signal a multi-file build regardless of length
_BUILD_KEYWORDS = re.compile(
    r"\b(website|webpage|web\s*app|landing\s*page|dashboard|saas|sass|scss|"
    r"react|vue|next\.?js|svelte|fullstack|full.stack|frontend|backend|"
    r"portfolio|blog|app|application|modern|complete)\b",
    re.I,
)


def _quick_reject(user_input: str) -> bool:
    """Return True when we can rule out parallel without an LLM call."""
    text = user_input.strip()
    if not text or text.startswith("/"):
        return True
    # Build keywords always go to the LLM planner regardless of length
    if _BUILD_KEYWORDS.search(text):
        return False
    if len(text.split()) < 8:
        return True  # very short → almost certainly not a multi-component build
    if _SINGLE_AGENT_STARTERS.match(text):
        return True
    return False


# ---------------------------------------------------------------------------
# DAG execution
# ---------------------------------------------------------------------------

def _topo_waves(features: List[FeatureSpec]) -> List[List[FeatureSpec]]:
    """Group features into waves where each wave can run fully in parallel."""
    remaining = {f.id: f for f in features}
    completed: set[str] = set()
    waves: list[list[FeatureSpec]] = []

    while remaining:
        ready = [
            f for f in remaining.values()
            if all(dep in completed for dep in f.depends_on)
        ]
        if not ready:
            ready = list(remaining.values())  # circular dep guard
        waves.append(ready)
        for f in ready:
            completed.add(f.id)
            del remaining[f.id]

    return waves


# ---------------------------------------------------------------------------
# Feature agent prompt
# ---------------------------------------------------------------------------

def _build_feature_prompt(
    feature: FeatureSpec,
    spec: ProjectSpec,
    prior_outputs: Dict[str, str],
) -> str:
    lines = [
        f"PROJECT: {spec.project_name}",
        f"OVERALL GOAL: {spec.description}",
    ]
    if spec.tech_stack:
        lines.append(f"TECH STACK: {json.dumps(spec.tech_stack)}")
    lines += [
        "",
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
        lines.append("ALREADY BUILT BY PARALLEL AGENTS (use these interfaces):")
        for dep_id, summary in prior_outputs.items():
            if dep_id in feature.depends_on:
                lines.append(f"  [{dep_id}]: {summary[:400]}")
        lines.append("")
    lines.append(
        "Build this component completely. Create ALL files. No placeholders or TODOs.\n"
        "When done, reply with a concise summary of what you built: key file paths, "
        "ports, API routes, and exported interfaces — so the integrator can wire "
        "everything together."
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
    """Thread-safe Rich Live dashboard for parallel agents."""

    def __init__(self, features: List[FeatureSpec]) -> None:
        import threading
        from agent.ui.console import console as _console

        self._statuses: Dict[str, str] = {f.id: "pending" for f in features}
        self._names:    Dict[str, str]  = {f.id: f.name   for f in features}
        self._order:    List[str]       = [f.id for f in features]
        self._console = _console
        self._live = None
        self._lock  = threading.Lock()

    def _render(self):
        import time as _time
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich.console import Group

        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=3,  no_wrap=True)
        grid.add_column(width=24, no_wrap=True)
        grid.add_column(width=10, no_wrap=True)
        grid.add_column(no_wrap=True)

        now = _time.monotonic()
        for fid in self._order:
            status = self._statuses.get(fid, "pending")
            name   = self._names.get(fid, fid)
            icon   = _STATUS_ICON.get(status, "·")
            style  = _STATUS_STYLE.get(status, "dim")

            if status == "running":
                filled = int((now % 5.0) / 5.0 * _BAR_CHARS)
            elif status == "done":
                filled = _BAR_CHARS
            else:
                filled = 0

            bar = Text()
            bar.append("█" * filled,              style=style)
            bar.append("░" * (_BAR_CHARS - filled), style="dim")

            grid.add_row(
                Text(icon,   style=style),
                Text(name,   style="bold white" if status == "running" else "white"),
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

    def start(self) -> None:
        from rich.live import Live
        with self._lock:
            self._live = Live(
                self._render(),
                console=self._console,
                refresh_per_second=8,
                transient=False,
                auto_refresh=True,
            )
            self._live.start()

    def stop(self) -> None:
        with self._lock:
            if self._live:
                self._live.update(self._render())
                self._live.stop()
                self._live = None

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    def set(self, feature_id: str, status: str) -> None:
        with self._lock:
            self._statuses[feature_id] = status
            self._refresh()

    def running(self, ids: List[str]) -> None:
        with self._lock:
            for fid in ids:
                self._statuses[fid] = "running"
            self._refresh()

    def done(self, fid: str) -> None:
        with self._lock:
            self._statuses[fid] = "done"
            self._refresh()

    def failed(self, fid: str) -> None:
        with self._lock:
            self._statuses[fid] = "failed"
            self._refresh()


# ---------------------------------------------------------------------------
# Feature agent runner
# ---------------------------------------------------------------------------

def _run_feature_agent(
    feat: FeatureSpec,
    prompt: str,
    max_turns: int,
    skill_context: str,
) -> str:
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
# Integration — scaled to actual complexity
# ---------------------------------------------------------------------------

def _build_integration_prompt(
    spec: ProjectSpec,
    outputs: Dict[str, str],
    original_request: str,
) -> str:
    lines = [
        f"INTEGRATION TASK for: {spec.project_name}",
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
        "5. Run the project to verify it starts.\n"
        "Summarize what was built and how to run it."
    )
    return "\n".join(lines)


def _build_verifier_prompt(
    spec: ProjectSpec,
    outputs: Dict[str, str],
    original_request: str,
) -> str:
    lines = [
        f"VERIFICATION for: {spec.project_name}",
        f"ORIGINAL REQUEST: {original_request}",
        "", "FEATURE OUTPUTS:",
    ]
    for fid, summary in outputs.items():
        lines.append(f"\n--- {fid} ---\n{summary[:600]}")
    lines.append(
        "\n\nVerify that all components connect correctly:\n"
        "- imports/exports match\n"
        "- API endpoints are correctly defined and called\n"
        "- ports are consistent\n"
        "- CORS is configured\n"
        "- env var names are consistent\n"
        "Report specific mismatches. Do NOT modify files."
    )
    return "\n".join(lines)


def _scaled_integration_tasks(
    spec: ProjectSpec,
    outputs: Dict[str, str],
    original_request: str,
    skill_context: str,
) -> List[dict]:
    """Return only the integration tasks actually warranted by the build size."""
    n = len(spec.features)
    tasks = []

    # Always: one main integrator that fixes + glues + docs + validates
    tasks.append({
        "description": "Integrator",
        "prompt": _build_integration_prompt(spec, outputs, original_request),
        "max_turns": 40,
    })

    # 3+ features: add an independent verifier for cross-checking
    if n >= 3:
        tasks.append({
            "description": "Verifier",
            "prompt": _build_verifier_prompt(spec, outputs, original_request),
            "max_turns": 20,
        })

    return tasks


def _run_integration(
    spec: ProjectSpec,
    outputs: Dict[str, str],
    original_request: str,
    skill_context: str,
    max_turns: int,
) -> str:
    tasks = _scaled_integration_tasks(spec, outputs, original_request, skill_context)

    n_tasks = len(tasks)
    if n_tasks == 1:
        # Single integrator — run directly, no overhead
        feat = FeatureSpec(
            id="integrator",
            name=tasks[0]["description"],
            description=tasks[0]["prompt"],
        )
        return _run_feature_agent(feat, tasks[0]["prompt"], tasks[0]["max_turns"], skill_context)

    ui.info(f"  Running {n_tasks} integration agents in parallel...")
    result = spawn_agents_parallel(tasks, max_concurrency=n_tasks)
    return f"INTEGRATION COMPLETE for {spec.project_name}\n\n{result}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_parallel_build(
    user_input: str,
    skill_context: str = "",
    project_context: str = "",
    max_turns_per_agent: int = 60,
    on_progress: Optional[Callable[[str, str], None]] = None,
) -> Optional[str]:
    """Decide (via LLM) whether to run parallel agents; if yes, run them.

    Returns the final integrated output string, or None when the normal
    single-agent loop should handle the request instead.

    Parameters
    ----------
    user_input:       The user's raw request.
    skill_context:    Composed skill prompts injected into each agent.
    project_context:  Current project map / graph context for the LLM planner.
    max_turns_per_agent: Tool-call turn budget per feature agent.
    on_progress:      Optional callback(feature_id, status).
    """
    # Fast reject — don't even call LLM for obvious single-agent work
    if _quick_reject(user_input):
        return None

    ui.muted("  Planning agents...")
    spec = _llm_plan_agents(user_input, project_context)

    if spec is None:
        return None  # LLM decided single agent is fine

    n = len(spec.features)
    ui.info(
        f"  Project: {spec.project_name}  ·  "
        f"{n} agent{'s' if n != 1 else ''}  ·  "
        + (f"stack: {', '.join(spec.tech_stack.values())}" if spec.tech_stack else "")
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
                def _worker(feat: FeatureSpec) -> tuple[str, str]:
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

        # Add integrator row to dashboard
        with progress._lock:
            progress._order.append("integrator")
            progress._statuses["integrator"] = "running"
            progress._names["integrator"] = "Integrator"
            progress._refresh()

    finally:
        set_parallel_mode(False)
        progress.stop()

    ui.info("  Integrating...")
    final = _run_integration(spec, outputs, user_input, skill_context, max_turns_per_agent)
    return final
