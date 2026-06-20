"""Project Planner — Planner-First Orchestration Architecture (KRYTH V2).

Replaces keyword/heuristic-based orchestration decisions with a structured
LLM planning phase. The planner runs BEFORE any DAG is built, any team is
generated, or any agent is spawned.

The planner becomes the SOURCE OF TRUTH for:
  * Project understanding (type, goals, features, tech stack)
  * Work Breakdown Structure (modules with goals, files, deliverables)
  * Dependency Graph (which modules depend on which)
  * Agent Contracts (what each team owns and must deliver)
  * Execution Mode (DIRECT / DAG / SWARM — from the plan, not keywords)

Fallback: if the planner fails (LLM down, timeout, parse error) the
existing pipeline runs unchanged. This is purely additive.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Import at module level so tests can patch it
try:
    from agent.llm import ask_llm_stream
except Exception:
    ask_llm_stream = None  # type: ignore[assignment]


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class DeliverableContract:
    """V3: Full contract for a module — inputs, outputs, success criteria.

    Workers receive this as their ONLY source of truth.
    Workers do NOT create scope, do NOT re-plan.
    """
    module_name: str
    goal: str
    inputs: List[str] = field(default_factory=list)       # what this module consumes
    outputs: List[str] = field(default_factory=list)      # what this module produces
    files_to_create: List[str] = field(default_factory=list)
    files_to_modify: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list) # module names
    success_criteria: List[str] = field(default_factory=list)
    # Validation status (set after execution)
    fulfilled: bool = False
    fulfillment_notes: str = ""

    def to_worker_brief(self) -> str:
        """Compact contract string for injection into agent system prompts."""
        lines = [
            f"CONTRACT: {self.module_name}",
            f"Goal: {self.goal}",
        ]
        if self.inputs:
            lines.append(f"Inputs: {', '.join(self.inputs[:4])}")
        if self.outputs:
            lines.append(f"Outputs: {', '.join(self.outputs[:4])}")
        if self.files_to_create:
            lines.append(f"Create: {', '.join(self.files_to_create[:5])}")
        if self.success_criteria:
            lines.append(f"Done when: {'; '.join(self.success_criteria[:3])}")
        lines.append("You own this scope. Do NOT re-plan or re-scope.")
        return "\n".join(lines)


@dataclass
class ProjectMilestone:
    """V3: A structured execution checkpoint grouping related modules.

    Milestones are executed sequentially. Within a milestone, modules
    that have no inter-dependencies run in parallel (DAG layers).
    """
    name: str           # e.g. "Milestone 1 — Foundation"
    order: int          # 1-based execution order
    modules: List[str]  # module names in this milestone
    goal: str = ""      # what this milestone achieves
    is_critical: bool = False  # on the critical path
    # Execution results (set after milestone completes)
    completed: bool = False
    approved: bool = False
    deliverables_planned: int = 0
    deliverables_completed: int = 0

    @property
    def progress_pct(self) -> float:
        if self.deliverables_planned == 0:
            return 100.0 if self.completed else 0.0
        return self.deliverables_completed / self.deliverables_planned * 100


@dataclass
class ProjectModule:
    """One logical unit of work — becomes one agent team in DAG mode.

    Named after business capability (e.g. "Authentication", "AI Matching"),
    NOT a technical layer ("Backend", "Frontend").
    """
    name: str
    goal: str
    files_owned: List[str] = field(default_factory=list)
    deliverables: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)   # other module names
    success_criteria: List[str] = field(default_factory=list)
    estimated_turns: int = 30
    risk: str = "low"   # low / medium / high
    # V3: enhanced contract fields
    inputs: List[str] = field(default_factory=list)   # what this module consumes
    outputs: List[str] = field(default_factory=list)  # what this module produces

    @property
    def id(self) -> str:
        """Stable identifier derived from name."""
        return re.sub(r"[^a-z0-9]+", "_", self.name.lower()).strip("_")

    def to_contract(self) -> DeliverableContract:
        """Generate the deliverable contract for this module."""
        return DeliverableContract(
            module_name=self.name,
            goal=self.goal,
            inputs=self.inputs,
            outputs=self.outputs or self.deliverables,
            files_to_create=[f for f in self.files_owned if not f.endswith("_existing")],
            files_to_modify=[],
            dependencies=self.dependencies,
            success_criteria=self.success_criteria,
        )


@dataclass
class ProjectPlan:
    """Complete understanding of a project produced by the Planner LLM call.

    V3: Now includes structured milestones, deliverable contracts, and
    critical path identification. This is the single source of truth for:
    - DAG node creation
    - Milestone-driven execution
    - Deliverable contracts per team
    - Critical path optimization
    - Mission scorecard
    """
    project_name: str
    project_type: str       # "saas" | "api" | "library" | "cli" | "website" | "tool" | "other"
    goal: str
    features: List[str] = field(default_factory=list)
    tech_stack: Dict[str, str] = field(default_factory=dict)  # role → technology
    modules: List[ProjectModule] = field(default_factory=list)
    milestones: List[str] = field(default_factory=list)         # V2: legacy string list
    structured_milestones: List[ProjectMilestone] = field(default_factory=list)  # V3
    estimated_files: int = 0
    risks: List[str] = field(default_factory=list)
    # Execution recommendation derived from the dependency graph
    recommended_mode: str = "direct"  # "direct" | "dag" | "swarm"
    parallel_streams: int = 1
    estimated_speedup: float = 1.0
    raw_llm_output: str = ""

    @property
    def is_trivial(self) -> bool:
        """True when the project is simple enough to skip orchestration."""
        return len(self.modules) <= 1

    @property
    def independent_modules(self) -> List[ProjectModule]:
        """Modules with no dependencies — can run in parallel immediately."""
        dep_names = {dep for m in self.modules for dep in m.dependencies}
        return [m for m in self.modules if not m.dependencies]

    def dependency_layers(self) -> List[List[ProjectModule]]:
        """Topological layers — modules in the same layer are independent."""
        completed: set = set()
        remaining = list(self.modules)
        layers: List[List[ProjectModule]] = []
        while remaining:
            ready = [
                m for m in remaining
                if all(dep in completed for dep in m.dependencies)
            ]
            if not ready:
                ready = remaining[:]   # circular / unknown → run together
            layers.append(ready)
            for m in ready:
                completed.add(m.name)
            ready_names = {m.name for m in ready}
            remaining = [m for m in remaining if m.name not in ready_names]
        return layers

    def to_summary(self) -> str:
        """Compact text for injecting into agent prompts."""
        lines = [
            f"Project: {self.project_name}",
            f"Goal: {self.goal}",
        ]
        if self.tech_stack:
            stack = ", ".join(f"{k}: {v}" for k, v in self.tech_stack.items())
            lines.append(f"Stack: {stack}")
        if self.features:
            lines.append(f"Features: {', '.join(self.features[:6])}")
        lines.append(f"Modules: {len(self.modules)}")
        return "\n".join(lines)

    # ── V3 methods ─────────────────────────────────────────────────────────────

    def ensure_structured_milestones(self) -> List["ProjectMilestone"]:
        """Build structured milestones from dependency layers if not already set.

        Groups dependency layers into milestones. Each layer where modules have
        no deps from the same layer runs as a single milestone.
        """
        if self.structured_milestones:
            return self.structured_milestones

        layers = self.dependency_layers()
        milestones: List[ProjectMilestone] = []

        # Identify critical path modules (longest chain)
        critical_names = self._critical_path_module_names()

        for i, layer in enumerate(layers, 1):
            module_names = [m.name for m in layer]
            # Milestone goal = first module's goal or derived
            goal = layer[0].goal if len(layer) == 1 else f"Complete {', '.join(module_names[:2])}"
            is_crit = any(m.name in critical_names for m in layer)
            ms = ProjectMilestone(
                name=f"Milestone {i} — {', '.join(module_names[:2])}",
                order=i,
                modules=module_names,
                goal=goal,
                is_critical=is_crit,
                deliverables_planned=sum(len(m.deliverables) for m in layer),
            )
            milestones.append(ms)

        self.structured_milestones = milestones
        return milestones

    def _critical_path_module_names(self) -> set:
        """Find module names on the critical path (longest dependency chain)."""
        mod_map = {m.name: m for m in self.modules}

        def depth(name: str, visited: set) -> int:
            if name in visited:
                return 0
            visited.add(name)
            m = mod_map.get(name)
            if not m or not m.dependencies:
                return 1
            return 1 + max((depth(dep, set(visited)) for dep in m.dependencies), default=0)

        # Find the module with greatest depth (end of critical path)
        deepest = max(self.modules, key=lambda m: depth(m.name, set()), default=None)
        if deepest is None:
            return set()

        # Walk backwards to find the critical path
        critical: set = set()
        def trace(name: str) -> None:
            critical.add(name)
            m = mod_map.get(name)
            if not m:
                return
            for dep in m.dependencies:
                trace(dep)

        trace(deepest.name)
        return critical

    def get_contracts(self) -> List[DeliverableContract]:
        """Generate deliverable contracts for all modules."""
        return [m.to_contract() for m in self.modules]

    def get_contract(self, module_name: str) -> Optional[DeliverableContract]:
        """Get the contract for a specific module."""
        for m in self.modules:
            if m.name == module_name or m.id == module_name:
                return m.to_contract()
        return None

    def critical_path(self) -> List[str]:
        """Return module names in critical path order (source → sink)."""
        critical_names = self._critical_path_module_names()
        # Order by dependency layers
        ordered = []
        for layer in self.dependency_layers():
            for m in layer:
                if m.name in critical_names:
                    ordered.append(m.name)
        return ordered


# ── Planner LLM prompt ─────────────────────────────────────────────────────────

_PLANNER_SYSTEM = """You are a senior engineering architect acting as CEO/Planner. Read the user's request and output a STRICT JSON object describing the full project plan with milestones and deliverable contracts.

Output ONLY valid JSON — no markdown, no prose, no code fences.

The JSON must conform to this schema:
{
  "project_name": "short name for this project",
  "project_type": "saas|api|library|cli|website|tool|other",
  "goal": "one sentence — what this project accomplishes",
  "features": ["feature 1", "feature 2", ...],
  "tech_stack": {"frontend": "...", "backend": "...", "database": "...", ...},
  "modules": [
    {
      "name": "Module Name (business capability — Authentication, AI Matching, NOT 'Backend')",
      "goal": "what this module delivers",
      "inputs": ["data or service this module consumes"],
      "outputs": ["concrete artifact this module produces"],
      "files_owned": ["path/to/file.py", "path/to/dir/"],
      "deliverables": ["concrete output 1", "concrete output 2"],
      "dependencies": ["Other Module Name"],
      "success_criteria": ["specific verifiable criterion"],
      "estimated_turns": 30,
      "risk": "low|medium|high"
    }
  ],
  "milestones": [
    {
      "name": "Milestone 1 — Foundation",
      "order": 1,
      "modules": ["Database", "Authentication"],
      "goal": "Set up data layer and auth"
    }
  ],
  "estimated_files": 12,
  "risks": ["risk 1", "risk 2"],
  "recommended_mode": "direct|dag|swarm",
  "parallel_streams": 3,
  "estimated_speedup": 2.4
}

Rules:
- modules: BUSINESS CAPABILITY names only (Authentication, AI Matching, Payment Processing)
  NEVER technical layers (Backend, Frontend, Database Team)
- inputs: what data/services/tokens this module RECEIVES from other modules
- outputs: what this module PRODUCES for other modules to consume
- dependencies: module NAMES that must complete BEFORE this module starts
- milestones: group modules into sequential checkpoints; modules in same milestone run in parallel
- recommended_mode: direct=1 sequential / dag=2-8 parallel streams / swarm=9+
- parallel_streams: max modules running concurrently at peak
- estimated_speedup: speedup vs single-agent (1.0 = no benefit)
- Output ONLY the JSON object. No explanation."""


def plan_project(
    user_input: str,
    project_context: str = "",
    *,
    timeout_s: float = 20.0,
) -> Optional[ProjectPlan]:
    """Run the Planner LLM call and return a structured ProjectPlan.

    Returns None on any failure — callers fall back to the existing pipeline.
    Never raises.
    """
    try:
        _messages = [
            {"role": "system", "content": _PLANNER_SYSTEM},
            {"role": "user", "content": _build_planner_prompt(user_input, project_context)},
        ]
        _llm_fn = ask_llm_stream
        if _llm_fn is None:
            try:
                from agent.llm import ask_llm_stream as _llm_fn  # type: ignore[assignment]
            except Exception:
                return None
        response = _llm_fn(_messages, tools=None)
        raw = (response.get("content") or "").strip()
        if not raw:
            return None
        plan = _parse_plan(raw, user_input)
        if plan is not None:
            plan.raw_llm_output = raw
        return plan
    except Exception:
        return None


def _build_planner_prompt(user_input: str, project_context: str) -> str:
    ctx = f"\n\nProject context (existing code):\n{project_context[:1500]}" if project_context else ""
    return f"Plan this project:\n\n{user_input}{ctx}"


def _parse_plan(raw: str, user_input: str) -> Optional[ProjectPlan]:
    """Parse the LLM JSON response into a ProjectPlan. Returns None if invalid."""
    # Strip markdown fences if present
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
        stripped = stripped.strip()

    # Extract first balanced JSON object
    obj = _extract_json_object(stripped)
    if not obj:
        return None

    try:
        modules = []
        for m in (obj.get("modules") or []):
            if not isinstance(m, dict):
                continue
            name = str(m.get("name", "")).strip()
            if not name:
                continue
            modules.append(ProjectModule(
                name=name,
                goal=str(m.get("goal", "")),
                files_owned=_str_list(m.get("files_owned")),
                deliverables=_str_list(m.get("deliverables")),
                dependencies=_str_list(m.get("dependencies")),
                success_criteria=_str_list(m.get("success_criteria")),
                estimated_turns=int(m.get("estimated_turns", 30)),
                risk=str(m.get("risk", "low")).lower(),
                # V3: inputs/outputs for deliverable contracts
                inputs=_str_list(m.get("inputs")),
                outputs=_str_list(m.get("outputs")),
            ))

        if not modules:
            return None

        # V3: Parse structured milestones (new format) or fall back to strings
        structured_milestones: List[ProjectMilestone] = []
        raw_ms = obj.get("milestones") or []
        legacy_milestones: List[str] = []
        for i, ms in enumerate(raw_ms, 1):
            if isinstance(ms, dict):
                ms_modules = _str_list(ms.get("modules"))
                structured_milestones.append(ProjectMilestone(
                    name=str(ms.get("name", f"Milestone {i}")),
                    order=int(ms.get("order", i)),
                    modules=ms_modules,
                    goal=str(ms.get("goal", "")),
                    deliverables_planned=sum(
                        len(m.deliverables) for m in modules
                        if m.name in ms_modules
                    ),
                ))
            else:
                legacy_milestones.append(str(ms))

        mode = str(obj.get("recommended_mode", "direct")).lower()
        if mode not in ("direct", "dag", "swarm"):
            mode = "dag" if len(modules) >= 3 else "direct"

        return ProjectPlan(
            project_name=str(obj.get("project_name", user_input[:40])),
            project_type=str(obj.get("project_type", "other")).lower(),
            goal=str(obj.get("goal", "")),
            features=_str_list(obj.get("features")),
            tech_stack=_str_dict(obj.get("tech_stack")),
            modules=modules,
            milestones=legacy_milestones,
            structured_milestones=structured_milestones,
            estimated_files=int(obj.get("estimated_files", 0)),
            risks=_str_list(obj.get("risks")),
            recommended_mode=mode,
            parallel_streams=int(obj.get("parallel_streams", 1)),
            estimated_speedup=float(obj.get("estimated_speedup", 1.0)),
        )
    except Exception:
        return None


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    return None
    return None


def _str_list(val) -> List[str]:
    if not val:
        return []
    if isinstance(val, list):
        return [str(v) for v in val if v]
    return [str(val)]


def _str_dict(val) -> Dict[str, str]:
    if not isinstance(val, dict):
        return {}
    return {str(k): str(v) for k, v in val.items() if k and v}


# ── Plan → TaskDAG conversion ──────────────────────────────────────────────────

def dag_from_plan(plan: ProjectPlan) -> "TaskDAG":
    """Convert a ProjectPlan into a TaskDAG.

    One TaskNode per module. Dependencies carry forward from the plan.
    This replaces the heuristic DAG built from capability names.
    """
    from agent.orchestration.task_dag import TaskDAG, TaskNode, RiskLevel

    _RISK = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH}

    dag = TaskDAG(name=plan.project_name)
    for m in plan.modules:
        node = TaskNode(
            id=m.id,
            name=m.name,
            description=m.goal,
            capabilities_required=[m.name.lower()],
            dependencies=[
                re.sub(r"[^a-z0-9]+", "_", dep.lower()).strip("_")
                for dep in m.dependencies
            ],
            risk=_RISK.get(m.risk, RiskLevel.LOW),
            estimated_turns=m.estimated_turns,
            affected_dirs=[f for f in m.files_owned if f.endswith("/")],
            affected_files=[f for f in m.files_owned if not f.endswith("/")],
            validation=m.success_criteria,
            is_blocking=m.risk in ("high", "critical"),
        )
        dag.add(node)
    return dag


# ── Plan → TeamPlan conversion ─────────────────────────────────────────────────

def team_from_plan(
    plan: ProjectPlan,
    dag: "TaskDAG",
    user_input: str = "",
) -> "TeamPlan":
    """Generate a TeamPlan directly from a ProjectPlan.

    One AgentRole per module — named after the business capability, not a
    technical domain. Dependencies are wired from the plan's dependency graph.
    """
    from agent.orchestration.team_generator import AgentRole, OwnedScope, TeamPlan

    agents = []
    for m in plan.modules:
        owned_files  = [f for f in m.files_owned if not f.endswith("/")]
        owned_dirs   = [f.rstrip("/") for f in m.files_owned if f.endswith("/")]

        mission_lines = [
            f"You own the {m.name} module.",
            f"Goal: {m.goal}",
        ]
        if m.deliverables:
            mission_lines.append(f"Deliver: {', '.join(m.deliverables[:4])}")
        if m.success_criteria:
            mission_lines.append(f"Success when: {'; '.join(m.success_criteria[:3])}")

        dep_ids = [
            re.sub(r"[^a-z0-9]+", "_", dep.lower()).strip("_")
            for dep in m.dependencies
        ]

        agents.append(AgentRole(
            id=m.id,
            role=f"{m.name} Team",
            mission="\n".join(mission_lines),
            owns=OwnedScope(files=owned_files, directories=owned_dirs),
            task_node_ids=[m.id],
            dependencies=dep_ids,
            validation_rules=m.success_criteria,
            max_turns=m.estimated_turns,
        ))

    # Estimate cost from plan metadata
    n = len(agents)
    total_turns = sum(m.estimated_turns for m in plan.modules)
    est_tokens  = total_turns * 3000   # rough estimate
    layers = plan.dependency_layers()

    return TeamPlan(
        agents=agents,
        complexity=float(n),
        risk_assessment=max((m.risk for m in plan.modules), default="low",
                           key=lambda r: {"low": 0, "medium": 1, "high": 2}.get(r, 0)),
        estimated_total_turns=total_turns,
        estimated_total_tokens=est_tokens,
        parallel_benefit=plan.estimated_speedup,
        parallel_cost=max(1.0, n * 0.15),
        recommended_strategy="parallel" if plan.recommended_mode != "direct" else "sequential",
        reasoning=(
            f"Planner-generated team: {n} modules, "
            f"{plan.parallel_streams} parallel streams, "
            f"{plan.estimated_speedup:.1f}x speedup. "
            f"Mode: {plan.recommended_mode.upper()}"
        ),
        layer_count=len(layers),
    )


# ── DAG eligibility from plan ──────────────────────────────────────────────────

def mode_from_plan(plan: ProjectPlan) -> str:
    """Return recommended execution mode from the plan structure.

    Validates the planner's recommendation against the actual module count
    and dependency structure. The planner's own recommendation takes priority
    unless the structure clearly contradicts it.
    """
    n = len(plan.modules)
    if n <= 1:
        return "direct"

    # Count layers to find true parallelism
    layers = plan.dependency_layers()
    n_layers = len(layers)

    # Maximum concurrent modules across all layers
    max_parallel = max(len(l) for l in layers) if layers else 1

    # Honour planner's swarm recommendation for large plans
    if plan.recommended_mode == "swarm" and n >= 9:
        return "swarm"
    # Completely sequential chain (every layer has 1 module) → could still be DAG
    # if the planner thinks so, respect that
    if max_parallel <= 1 and n_layers >= n:
        # Truly sequential — single-agent is more efficient
        return "direct"
    # Planner recommends DAG or SWARM and there's actual parallelism
    if plan.recommended_mode in ("dag", "swarm") and max_parallel >= 2:
        return "dag" if n < 9 else "swarm"
    # Small plans with some parallelism
    if n >= 3 and max_parallel >= 2:
        return "dag"
    if n >= 2 and plan.parallel_streams >= 2:
        return "dag"
    return "direct"


# ── Plan rendering ─────────────────────────────────────────────────────────────

def render_plan_panel(plan: ProjectPlan) -> None:
    """V3: Render the full project plan as a Rich panel with milestones and critical path."""
    try:
        import rich.box
        from rich.console import Group
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from agent.ui.console import console
        from agent.ui.panels import _print_panel
        from agent.ui.theme import CORE

        # Header
        head = Text()
        head.append(f"  {plan.project_name}\n", style="bold white")
        head.append(f"  {plan.goal}\n", style="dim")
        if plan.tech_stack:
            stack_str = "  ".join(f"{k}: {v}" for k, v in list(plan.tech_stack.items())[:5])
            head.append(f"  Stack: {stack_str}\n", style="dim cyan")

        # V3: Milestone tree view
        milestones = plan.ensure_structured_milestones()
        critical_names = set(plan.critical_path())
        mod_map = {m.name: m for m in plan.modules}

        ms_body = Text()
        for ms in milestones:
            crit_marker = " ⚑" if ms.is_critical else ""
            ms_body.append(f"\n  Milestone {ms.order}: {ms.name}{crit_marker}\n",
                           style="bold bright_cyan" if ms.is_critical else "bold white")
            for mod_name in ms.modules:
                mod = mod_map.get(mod_name)
                if mod:
                    deliv = ", ".join(mod.deliverables[:2]) or mod.goal[:35]
                    risk_marker = " ⚠" if mod.risk == "high" else ""
                    on_crit = " ⚑" if mod_name in critical_names else ""
                    ms_body.append(f"    {'→' if mod.dependencies else '•'} ", style="dim")
                    ms_body.append(f"{mod_name}{on_crit}", style="bold cyan" if mod_name in critical_names else "white")
                    ms_body.append(f"{risk_marker}  {deliv[:38]}\n", style="dim")

        # Critical path
        cp = plan.critical_path()
        cp_text = Text()
        if cp:
            cp_text.append("\n  Critical Path: ", style="dim yellow")
            cp_text.append(" → ".join(cp[:6]), style="yellow")
            if len(cp) > 6:
                cp_text.append(f" (+{len(cp)-6} more)", style="dim")
            cp_text.append("\n")

        # Stats
        mode_color = {"direct": "white", "dag": "bright_cyan", "swarm": "bright_green"}.get(
            plan.recommended_mode, "white")
        stats = Text()
        stats.append(f"\n  Mode: ", style="dim")
        stats.append(plan.recommended_mode.upper(), style=f"bold {mode_color}")
        stats.append(f"  |  Modules: {len(plan.modules)}", style="dim")
        stats.append(f"  |  Milestones: {len(milestones)}", style="dim")
        stats.append(f"  |  Streams: {plan.parallel_streams}", style="dim")
        stats.append(f"  |  Speedup: {plan.estimated_speedup:.1f}x", style="dim")
        if plan.estimated_files:
            stats.append(f"  |  Est. files: {plan.estimated_files}", style="dim")

        renderable = Group(head, ms_body, cp_text, stats)
        _print_panel(Panel(
            renderable,
            title=Text.assemble((CORE, "kryth.core"), ("  MISSION PLAN", "kryth.core")),
            title_align="left",
            border_style="mission.border",
            padding=(1, 2),
            expand=False,
            box=rich.box.DOUBLE_EDGE,
        ))
    except Exception:
        try:
            from agent import ui
            ui.muted(f"  Plan: {plan.project_name} — {len(plan.modules)} modules")
            for m in plan.modules:
                ui.muted(f"  • {m.name}: {m.goal[:60]}")
        except Exception:
            pass
