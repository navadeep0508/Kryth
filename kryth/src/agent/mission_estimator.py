"""Mission Cost Estimator + DAG Eligibility Engine.

Phases 11 + 15, revised: decide DIRECT vs DAG vs SWARM for a task. The original
engine leaned on *file count* and *expected speedup*, which mis-routed highly
parallelizable single-file work — a SaaS landing page with hero/features/pricing/
FAQ/testimonials is one HTML file but six independent work streams. Those were
sent DIRECT because "1 file, low speedup".

The fix is a **Parallel Work Density** signal: how many independent work streams
exist *regardless of output file count*. Density — not file count — is the
dominant routing signal:

    NONE / LOW   → DIRECT        (no real parallel work)
    MEDIUM       → DIRECT or DAG (decided by expected speedup)
    HIGH         → DAG           (even if the output is a single file)
    VERY_HIGH    → SWARM         (many independent streams / platform-scale)

Density is computed from component/domain count, UI/content **section** count,
independent deliverables, and per-domain complexity (design, UI, backend, test,
docs). **Task Decomposition Potential** estimates how much work can proceed
independently before a merge step.

This module is a PURE estimator (no side effects, no runtime imports). The agent
loop consults it to pick an execution mode; the user can always override with
/mode.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ── Signals ──────────────────────────────────────────────────────────────────

# Independent component domains in a build request. Each present domain is a
# roughly-independent unit that can run in parallel.
_COMPONENT_PATTERNS = {
    "frontend": re.compile(r"\b(frontend|front.end|ui|react|vue|svelte|next\.?js|html|css|client|landing|website|web\s?site|page)\b", re.I),
    "backend": re.compile(r"\b(backend|back.end|api|server|endpoint|rest|graphql|fastapi|flask|express|controller|route)\b", re.I),
    "database": re.compile(r"\b(database|db|schema|migration|sql|postgres|mysql|mongo|orm|model)\b", re.I),
    "auth": re.compile(r"\b(auth|authentication|authorization|login|signup|jwt|oauth|session)\b", re.I),
    "payments": re.compile(r"\b(payment|stripe|billing|checkout|subscription)\b", re.I),
    "tests": re.compile(r"\b(test|tests|testing|pytest|jest|spec|coverage)\b", re.I),
    "docs": re.compile(r"\b(docs|documentation|readme|guide|tutorial)\b", re.I),
    "deploy": re.compile(r"\b(deploy|docker|ci/?cd|kubernetes|pipeline|infra)\b", re.I),
}

# UI / content sections. Each named section is an independent deliverable that
# can be built in parallel even when the whole thing renders to one file. This is
# the signal the old engine was blind to.
_SECTION_PATTERNS = {
    "hero": r"\bhero\b",
    "features": r"\bfeatures?\b",
    "pricing": r"\bpricing\b",
    "faq": r"\bfaqs?\b",
    "testimonials": r"\btestimonials?\b",
    "footer": r"\bfooter\b",
    "header": r"\bheader\b",
    "navbar": r"\b(navbar|nav\s?bar|navigation)\b",
    "cta": r"\b(cta|call.to.action)\b",
    "gallery": r"\b(gallery|carousel|slider)\b",
    "contact": r"\bcontact\b",
    "about": r"\babout\b",
    "team": r"\bteam\b",
    "blog": r"\bblog\b",
    "newsletter": r"\bnewsletter\b",
    "stats": r"\b(stats|statistics|metrics\s+section)\b",
    "banner": r"\bbanner\b",
    "portfolio": r"\bportfolio\b",
    "services": r"\bservices?\b",
    "clients": r"\b(clients|partners|logos)\b",
    "roadmap": r"\broadmap\b",
    "dashboard": r"\bdashboard\b",
    "sidebar": r"\bsidebar\b",
}

# Complexity multipliers — work that deepens a stream rather than adding one.
_COMPLEXITY_SIGNALS = {
    "animations": r"\b(animations?|transitions?|motion|parallax)\b",
    "responsive": r"\b(responsive|mobile.first|breakpoints?)\b",
    "interactive": r"\b(interactive|realtime|real.time|live\s+updates?|websocket)\b",
    "i18n": r"\b(i18n|internationaliz|localization|multi.language)\b",
    "accessibility": r"\b(accessibility|a11y|wcag)\b",
    "darkmode": r"\b(dark\s?mode|theming|themes?)\b",
}

# CRUD implies multiple endpoints/operations → a medium-density expansion.
_CRUD_RE = re.compile(r"\b(crud|rest\s+api|resourceful|endpoints?)\b", re.I)
# A design system / component library is many independent components.
_DESIGN_SYSTEM_RE = re.compile(r"\b(design\s+system|component\s+library|ui\s+kit|storybook)\b", re.I)
# Documentation projects decompose by section/page.
_DOCS_PROJECT_RE = re.compile(r"\b(documentation\s+(site|project|portal)|docs\s+site|api\s+reference|guide\s+series)\b", re.I)

# Platform-scale umbrella terms → many domains at once → VERY_HIGH by definition.
_PLATFORM_RE = re.compile(
    r"\b(full.?stack|web\s?app|webapp|web\s+application|saas\s+(platform|app|product|starter|kit|boilerplate|template)|"
    r"\bplatform\b|marketplace|social\s+(network|app)|task\s+manager|"
    r"management\s+system|admin\s+panel|e.?commerce\s+(site|store|platform)?|"
    r"(full|complete|entire|whole)\s+\w*\s*(app|application|system|platform|product))\b",
    re.I,
)
_PLATFORM_COMPONENTS = ("frontend", "backend", "database")

# Multi-section site terms (frontend-heavy, NOT full platform).
_SITE_RE = re.compile(r"\b(landing\s+page|landing|marketing\s+(site|page)|one.?pager|microsite)\b", re.I)

_FILE_EXT_RE = re.compile(r"\b[\w./-]+\.[a-z]{1,5}\b", re.I)
_AND_SPLIT_RE = re.compile(r"\s*(?:,|\band\b|\+)\s*", re.I)
_BUILD_RE = re.compile(r"\b(build|create|implement|develop|scaffold|generate|make|design)\b", re.I)
_TRIVIAL_RE = re.compile(r"\b(hello\.txt|hello world|typo|rename|print|one.?liner)\b", re.I)


# Density tiers (ordered) and the routing each implies.
_DENSITY_ORDER = ("none", "low", "medium", "high", "very_high")


@dataclass
class MissionEstimate:
    files: int
    independent_units: int
    dependency_depth: int
    agents: int
    tokens: int
    seq_time_s: float
    dag_time_s: float
    speedup: float
    recommendation: str          # "direct" | "dag" | "swarm"
    reason: str = ""
    # Parallel-work signals (new).
    parallel_density: str = "low"          # none | low | medium | high | very_high
    decomposition_potential: str = "low"   # low | medium | high | very_high
    complexity_score: int = 0              # 0–100
    coordination_cost_s: float = 0.0
    components: list = field(default_factory=list)
    sections: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files": self.files, "independent_units": self.independent_units,
            "dependency_depth": self.dependency_depth, "agents": self.agents,
            "tokens": self.tokens, "seq_time_s": round(self.seq_time_s, 1),
            "dag_time_s": round(self.dag_time_s, 1), "speedup": round(self.speedup, 2),
            "recommendation": self.recommendation, "reason": self.reason,
            "parallel_density": self.parallel_density,
            "decomposition_potential": self.decomposition_potential,
            "complexity_score": self.complexity_score,
            "coordination_cost_s": round(self.coordination_cost_s, 1),
            "components": self.components, "sections": self.sections,
        }


# Per-unit rough cost model (tunable; only RELATIVE values matter for the
# speedup ratio, which is what drives the MEDIUM-tier tie-break).
_SECONDS_PER_FILE = 8.0
_TOKENS_PER_FILE = 2500
_COORDINATION_OVERHEAD_S = 6.0   # per parallel wave (spawn + integrate)


# ── Parallel Work Density ────────────────────────────────────────────────────

@dataclass
class DensitySignal:
    components: list
    sections: list
    decomposition_units: int     # independent work streams before merge
    complexity_terms: list
    platform_scale: bool
    density: str                 # none | low | medium | high | very_high
    complexity_score: int        # 0–100


def parallel_density(text: str) -> DensitySignal:
    """Compute parallel-work density from request text.

    decomposition_units = distinct domains + named sections + expansion bonuses
    (CRUD endpoints, design-system components, docs sections). This is the count
    of work that can proceed independently before a merge — the headline signal.
    """
    text = text or ""

    components = [n for n, rx in _COMPONENT_PATTERNS.items() if rx.search(text)]
    sections = [n for n, pat in _SECTION_PATTERNS.items() if re.search(pat, text, re.I)]
    complexity_terms = [n for n, pat in _COMPLEXITY_SIGNALS.items() if re.search(pat, text, re.I)]

    platform_scale = bool(_PLATFORM_RE.search(text))
    is_site = bool(_SITE_RE.search(text))

    # Platform builds imply frontend+backend+database even if unstated.
    if platform_scale:
        for c in _PLATFORM_COMPONENTS:
            if c not in components:
                components.append(c)

    # Expansion bonuses: work that decomposes into several independent streams.
    bonus = 0
    if _CRUD_RE.search(text):
        bonus += 2                      # ~CRUD = several endpoints
    if _DESIGN_SYSTEM_RE.search(text):
        bonus += 5                      # component library = many components
    if _DOCS_PROJECT_RE.search(text):
        bonus += 3                      # docs project = several sections

    decomposition_units = len(components) + len(sections) + bonus

    # Trivial single-action requests have no parallel work.
    trivial = (
        bool(_TRIVIAL_RE.search(text))
        or (not components and not sections and not _BUILD_RE.search(text))
    )

    # Tier selection. Platform scale is VERY_HIGH by definition; site-with-sections
    # is at least HIGH; otherwise scale with decomposition units.
    if trivial and decomposition_units == 0:
        density = "none"
    elif platform_scale:
        density = "very_high"
    elif decomposition_units >= 8:
        density = "very_high"
    elif decomposition_units >= 4 or (is_site and len(sections) >= 3):
        density = "high"
    elif decomposition_units >= 2:
        density = "medium"
    elif decomposition_units >= 1:
        density = "low"
    else:
        density = "none"

    complexity_score = min(
        100,
        len(components) * 10 + len(sections) * 6 + len(complexity_terms) * 6 + bonus * 5,
    )

    return DensitySignal(
        components=components, sections=sections,
        decomposition_units=max(decomposition_units, 1 if density != "none" else 0),
        complexity_terms=complexity_terms, platform_scale=platform_scale,
        density=density, complexity_score=complexity_score,
    )


def _decomposition_label(units: int, density: str) -> str:
    if density == "very_high" or units >= 8:
        return "very_high"
    if density == "high" or units >= 4:
        return "high"
    if units >= 2:
        return "medium"
    return "low"


def estimate(user_input: str, profile=None) -> MissionEstimate:
    """Estimate mission cost + route DIRECT/DAG/SWARM.

    Delegates to the graph-driven ``orchestration_optimizer`` (work grouped by
    ownership domain; parallelism from the dependency graph — NOT section count),
    mapping its ExecutionPlan onto the MissionEstimate shape the UI/gate expect.
    Falls back to the legacy density model if the optimizer is unavailable.
    """
    text = user_input or ""
    try:
        from agent.orchestration_optimizer import optimize as _optimize
        plan = _optimize(text)
        g, c = plan.graph, plan.complexity
        # sections = frontend work items (shown in the panel as the breakdown)
        sections = []
        for a in plan.allocation.agents:
            if a["domain"] == "frontend":
                sections = [w for w in a["work_items"] if w != "frontend"]
        decomposition = ("very_high" if g.parallelism_class == "very_high"
                         else "high" if g.parallelism_class == "high"
                         else "medium" if g.total_nodes >= 4 else "low")
        return MissionEstimate(
            files=c.file_count, independent_units=max(1, plan.allocation.count),
            dependency_depth=c.dependency_depth, agents=max(1, plan.allocation.count),
            tokens=plan.cost.total_tokens, seq_time_s=plan.seq_eta.total_s,
            dag_time_s=plan.par_eta.total_s, speedup=plan.speedup,
            recommendation=plan.mode, reason=plan.reasoning,
            parallel_density=g.parallelism_class, decomposition_potential=decomposition,
            complexity_score=int(c.score), coordination_cost_s=plan.par_eta.coordination_s,
            components=list(g.nodes), sections=sections,
        )
    except Exception:
        pass

    # ── Legacy density fallback (kept for resilience) ────────────────────────
    sig = parallel_density(text)

    components = sig.components
    # independent_units is now the decomposition count, not just domain count —
    # this is what makes a multi-section single-file page parallelizable.
    independent_units = max(1, sig.decomposition_units)

    # File estimate: explicit extensions win; otherwise derive from decomposition
    # (each independent stream is ~1–2 files of real work).
    explicit_files = len(set(_FILE_EXT_RE.findall(text)))
    if explicit_files and not _BUILD_RE.search(text):
        files = explicit_files
    elif _BUILD_RE.search(text) and sig.decomposition_units >= 1:
        files = max(explicit_files, sig.decomposition_units)
    elif explicit_files:
        files = explicit_files
    else:
        listed = [t for t in _AND_SPLIT_RE.split(text) if t.strip()]
        files = max(1, min(len(listed), 6)) if len(listed) > 1 else 1

    # Dependency depth: cross-domain chains add a sequential floor.
    depth = 1
    if "database" in components and "backend" in components:
        depth += 1
    if ("backend" in components or "frontend" in components) and "tests" in components:
        depth += 1
    if "deploy" in components:
        depth += 1

    seq_time = files * _SECONDS_PER_FILE
    per_unit_files = files / independent_units
    parallel_compute = per_unit_files * _SECONDS_PER_FILE
    pipeline_fill = max(0, depth - 1) * _SECONDS_PER_FILE
    overhead = _COORDINATION_OVERHEAD_S * max(1, depth)
    dag_time = max(parallel_compute + pipeline_fill + overhead, _SECONDS_PER_FILE)
    speedup = seq_time / dag_time if dag_time > 0 else 1.0

    agents = _recommend_agents(files, independent_units)
    tokens = files * _TOKENS_PER_FILE
    decomposition = _decomposition_label(sig.decomposition_units, sig.density)

    rec, reason = _recommend(sig.density, speedup, independent_units, decomposition)

    return MissionEstimate(
        files=files, independent_units=independent_units, dependency_depth=depth,
        agents=agents, tokens=tokens, seq_time_s=seq_time, dag_time_s=dag_time,
        speedup=speedup, recommendation=rec, reason=reason,
        parallel_density=sig.density, decomposition_potential=decomposition,
        complexity_score=sig.complexity_score, coordination_cost_s=overhead,
        components=list(components), sections=list(sig.sections),
    )


def _recommend(density: str, speedup: float, units: int, decomposition: str) -> tuple:
    """Density is the dominant signal; speedup only breaks the MEDIUM tie.

    HIGH density routes to DAG *even if the output is one file* — that is the
    whole point of the fix.
    """
    if density in ("none", "low"):
        return "direct", f"{density} parallel density — no independent work streams; single-agent"
    if density == "medium":
        if speedup >= 1.2:
            return "dag", f"medium density, {speedup:.2f}x speedup across {units} streams — parallel stages"
        return "direct", f"medium density but low speedup ({speedup:.2f}x) — orchestration not worth it"
    if density == "high":
        return "dag", f"high density — {units} independent streams ({decomposition} decomposition); parallelize regardless of file count"
    return "swarm", f"very high density — platform-scale work across {units} streams; swarm"


def _recommend_agents(files: int, units: int) -> int:
    """Phase 4 dynamic scaling: agents from file + independent-unit counts.
    Never a fixed number; bounded for sanity."""
    by_files = 1
    if files >= 4:
        by_files = 2
    if files >= 10:
        by_files = 4
    if files >= 30:
        by_files = 8
    if files >= 80:
        by_files = 12
    if files >= 150:
        by_files = 15
    return max(1, min(max(by_files, units), max(units, 1) * 4, 16))


# ── Mode override (/mode) ────────────────────────────────────────────────────

_VALID_MODES = ("direct", "dag", "swarm", "auto")


def normalize_mode(m: str) -> Optional[str]:
    m = (m or "").strip().lower()
    return m if m in _VALID_MODES else None


def render(est: MissionEstimate) -> str:
    """Human-readable estimate block for the CLI."""
    comp = ", ".join(est.components) or "—"
    sect = ", ".join(est.sections) or "—"
    lines = [
        "  Mission estimate",
        f"    Complexity score   {est.complexity_score}/100",
        f"    Parallel density   {est.parallel_density.upper()}",
        f"    Decomposition      {est.decomposition_potential.upper()}  ({est.independent_units} independent streams)",
        f"    Components         {comp}",
        f"    Sections           {sect}",
        f"    Files (est)        {est.files}",
        f"    Coordination cost  {est.coordination_cost_s:.0f}s",
        f"    Sequential time    {est.seq_time_s:.0f}s",
        f"    Parallel time      {est.dag_time_s:.0f}s",
        f"    Expected speedup   {est.speedup:.2f}x",
        f"    Decision           {est.recommendation.upper()}  —  {est.reason}",
    ]
    return "\n".join(lines)
