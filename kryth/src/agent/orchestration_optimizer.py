"""Graph-driven orchestration optimizer.

Replaces the old heuristic/keyword complexity scorer + section-counting
parallelism model with a measurable, graph-based execution optimizer. The core
correction: **work is grouped by ownership domain, and parallelism is derived
from the dependency graph between domains — not from counting pages/sections.**

A SaaS landing page with hero/features/pricing/FAQ/testimonials is ONE frontend
owner (one agent), not five parallel agents. Parallelism only exists when two
*domains* (frontend vs backend vs database …) have no dependency between them.

Subsystems (all explainable, all returning breakdowns):
  1. Complexity Engine        — analyze_complexity()  → ComplexityBreakdown
  2. Dependency Graph Engine  — DependencyGraph        (cycles/critical path/width)
  3. Swarm Decision System    — swarm_benefit()        (benefit = savings − costs)
  4. Agent Allocation Engine  — allocate_agents()      (one agent per domain)
  5. Parallelism Analysis     — DependencyGraph.parallelism()
  6. Execution Mode Selector  — select_mode()          (reasoned Direct/DAG/Swarm)
  7. ETA Engine               — estimate_eta()         (per-mode breakdown)
  8. Cost Model               — cost_model()           (marginal-agent efficiency)
  9. Validation Layer         — validate_plan()        (rejects inflated plans)
 10. Reporting                — render_report()

Pure functions, no side effects, deterministic. ``optimize(text)`` is the entry
point returning a full ``ExecutionPlan``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ── Ownership domains + their canonical dependency model ──────────────────────
# Nodes in the graph are DOMAINS. Each maps to exactly one agent.
_DOMAIN_PATTERNS = {
    "frontend": re.compile(r"\b(frontend|front.end|ui|react|vue|svelte|next\.?js|html|css|client|landing|website|web\s?site|page|component)\b", re.I),
    "backend": re.compile(r"\b(backend|back.end|api|server|endpoint|rest|graphql|fastapi|flask|express|controller|route|service|crud)\b", re.I),
    "database": re.compile(r"\b(database|db|schema|migration|sql|postgres|mysql|mongo|orm|model|persistence)\b", re.I),
    "auth": re.compile(r"\b(auth|authentication|authorization|login|signup|jwt|oauth|session|sso)\b", re.I),
    "payments": re.compile(r"\b(payment|stripe|billing|checkout|subscription|invoice)\b", re.I),
    "infrastructure": re.compile(r"\b(infra|infrastructure|terraform|kubernetes|k8s|docker|ci/?cd|pipeline|provision|monitoring|logging|alerting|observability|grafana|prometheus|metrics)\b", re.I),
    "security": re.compile(r"\b(security|encryption|secrets?|vault|rbac|permissions?|audit)\b", re.I),
    "testing": re.compile(r"\b(test|tests|testing|pytest|jest|spec|coverage|e2e|integration\s+test)\b", re.I),
    "documentation": re.compile(r"\b(docs|documentation|readme|guide|tutorial|api\s+reference)\b", re.I),
    "deploy": re.compile(r"\b(deploy|deployment|release|rollout|ship)\b", re.I),
}

# Canonical upstream dependencies (domain → domains it depends on). Only edges
# between *present* domains are added to the graph.
_DOMAIN_DEPS = {
    "database": ["infrastructure"],
    "auth": ["database"],
    "backend": ["database", "auth", "infrastructure"],
    "payments": ["backend"],
    "frontend": ["backend"],          # soft (contract-first overlaps) — see _SOFT
    "security": ["auth", "backend"],
    "testing": ["backend", "frontend"],
    "deploy": ["backend", "frontend", "database", "testing"],
    "documentation": [],              # mostly independent, trails
    "infrastructure": [],
}
# Soft edges can overlap (contract-first); they still order but don't fully
# serialize. Used to model partial pipeline overlap in ETA.
_SOFT = {("frontend", "backend")}

# Sections/pages are work items WITHIN the frontend domain — NOT separate agents.
_SECTION_PATTERNS = re.compile(
    r"\b(hero|features?|pricing|faqs?|testimonials?|footer|header|navbar|nav\s?bar|"
    r"cta|gallery|carousel|contact|about|team|blog|newsletter|stats|banner|"
    r"portfolio|services?|clients?|roadmap|dashboard|sidebar|profile\s+page|settings\s+page)\b", re.I)

_BUILD_RE = re.compile(r"\b(build|create|implement|develop|scaffold|generate|make|design|add)\b", re.I)
_FIX_RE = re.compile(r"\b(fix|bug|patch|repair|debug|adjust|tweak|change|update)\b", re.I)
_PAGE_CTX = re.compile(r"\b(page|form|screen|view|widget|button)\b", re.I)
# Vague requests with no concrete target → clarification, not execution.
_AMBIGUOUS = re.compile(r"\b(improve|make\s+it\s+better|enhance|optimi[sz]e|clean\s+up|polish|refine|fix\s+stuff|do\s+better)\b", re.I)
_CONCRETE = re.compile(
    r"\b(button|colou?r|page|form|endpoint|api|database|db|auth|login|test|deploy|component|"
    r"feature|file|function|bug|dark\s?mode|crud|dashboard|schema|migration|payment|stripe|"
    r"frontend|backend|landing|site|blog|email|report|\.[a-z]{1,4})\b", re.I)
# Scale signals that escalate DAG → SWARM (each domain becomes a parallel team).
_SCALE = re.compile(
    r"\b(enterprise|notion.?scale|notion|google.?scale|massive|monorepo|million|"
    r"\d{2,}\s?k?\s*loc|\d{3,}k\b|large.?scale|at\s+scale|world.?class|hyperscale)\b", re.I)
# Content/work streams that parallelize independently (non-software fan-out).
_STREAM_VOCAB = {
    "blog": "blog", "blogs": "blog", "article": "article", "articles": "article",
    "tweet": "tweet", "tweets": "tweet", "post": "post", "posts": "post",
    "linkedin": "linkedin", "ad": "ad", "ads": "ad", "copy": "copy", "copies": "copy",
    "paper": "research", "papers": "research", "research": "research",
    "citation": "citation", "citations": "citation", "summary": "summary",
    "summaries": "summary", "report": "report", "reports": "report",
    "idea": "idea", "ideas": "idea", "plan": "plan", "deck": "deck",
    "strategy": "strategy", "forecast": "forecast", "email": "email", "emails": "email",
    "video": "video", "videos": "video", "newsletter": "newsletter",
}
_STREAM_WORD = re.compile(r"\b(" + "|".join(sorted(_STREAM_VOCAB, key=len, reverse=True)) + r")\b", re.I)
_FILE_EXT_RE = re.compile(r"\b[\w./-]+\.[a-z]{1,5}\b", re.I)
_RISK_HIGH = re.compile(r"\b(payment|auth|security|production|deploy|migration|encryption|credential)\b", re.I)
_UMBRELLA = re.compile(r"\b(full.?stack|web\s?app|webapp|web\s+application|saas\s+(platform|app|product|starter|kit)|"
                       r"\bplatform\b|marketplace|e.?commerce\s+(site|store|platform)?|"
                       r"management\s+system|social\s+(network|app))\b", re.I)
_UMBRELLA_DOMAINS = ("frontend", "backend", "database")
# A landing/marketing page/site is frontend-only — suppress the umbrella unless
# backend-ish work is explicitly requested.
_SITE = re.compile(r"\b(landing\s+page|landing|marketing\s+(site|page)|one.?pager|microsite|brochure)\b", re.I)
_EXPLICIT_BACKEND = re.compile(r"\b(backend|api|server|endpoint|database|db|auth|login|payment|crud)\b", re.I)

# Per-work-item rough cost (relative; only ratios matter for the decision).
_LOC_PER_ITEM = 120
_SECONDS_PER_ITEM = 8.0
_TOKENS_PER_ITEM = 2500
_AGENT_STARTUP_S = 3.0
_CONTEXT_LOAD_S = 4.0
_COORD_PER_LAYER_S = 5.0
_MERGE_PER_AGENT_S = 2.5
_VALIDATE_PER_ITEM_S = 1.0
_COMM_PER_EDGE_TOKENS = 400
_CONTEXT_XFER_PER_AGENT_TOKENS = 800


# ── Parsing: text → domains + work items ─────────────────────────────────────

@dataclass
class WorkItem:
    name: str
    domain: str
    loc: int = _LOC_PER_ITEM


def is_ambiguous(text: str) -> bool:
    """A vague request with no concrete target → clarification required."""
    t = (text or "").strip()
    if len(t.split()) <= 6 and _AMBIGUOUS.search(t) and not _CONCRETE.search(t):
        return True
    return False


def detect_streams(text: str) -> List[str]:
    """Independent content/work streams (non-software fan-out). Returns the
    distinct canonical stream labels — each becomes ONE agent (not one per item,
    so '100 blogs' is a single blog stream)."""
    found: List[str] = []
    for m in _STREAM_WORD.finditer(text or ""):
        canon = _STREAM_VOCAB.get(m.group(0).lower())
        if canon and canon not in found:
            found.append(canon)
    return found


def parse_work(text: str) -> Tuple[List[str], List[WorkItem]]:
    """Extract present domains and their work items. Sections collapse into the
    frontend domain (the key anti-inflation rule)."""
    text = text or ""
    domains: List[str] = [d for d, rx in _DOMAIN_PATTERNS.items() if rx.search(text)]
    # Umbrella terms imply frontend+backend+database — but NOT for a plain
    # landing/marketing page (frontend-only) unless backend work is explicit.
    site_only = _SITE.search(text) and not _EXPLICIT_BACKEND.search(text)
    if _UMBRELLA.search(text) and not site_only:
        for d in _UMBRELLA_DOMAINS:
            if d not in domains:
                domains.append(d)
    if site_only:
        domains = [d for d in domains if d == "frontend"] or ["frontend"]

    # Building an auth SYSTEM implies user storage + endpoints (database+backend).
    # But a login *page*/form, or a *fix*, does not — that stays a UI/single concern.
    building = _BUILD_RE.search(text) and not _FIX_RE.search(text)
    page_ctx = _PAGE_CTX.search(text)
    if "auth" in domains and building and not page_ctx:
        for d in ("database", "backend"):
            if d not in domains:
                domains.append(d)
    # CRUD / data-operations imply a persistence layer (and an API to serve it).
    if re.search(r"\bcrud\b", text, re.I) and not page_ctx:
        for d in ("backend", "database"):
            if d not in domains:
                domains.append(d)
    # Scale signals imply a full multi-team system.
    if _SCALE.search(text):
        for d in ("frontend", "backend", "database", "infrastructure", "testing"):
            if d not in domains:
                domains.append(d)

    items: List[WorkItem] = []
    sections = sorted(set(m.group(0).lower() for m in _SECTION_PATTERNS.finditer(text)))
    if sections and "frontend" not in domains:
        domains.append("frontend")
    for s in sections:
        items.append(WorkItem(name=s, domain="frontend"))     # sections → frontend work

    # Each non-frontend domain gets at least one representative work item; extra
    # items reflect breadth signals (e.g. "endpoints", "models").
    for d in domains:
        if d == "frontend" and items:
            continue
        n = 1
        if d == "backend" and re.search(r"\b(crud|endpoints?|rest)\b", text, re.I):
            n = 3
        if d == "database" and re.search(r"\b(models?|tables?|migrations?)\b", text, re.I):
            n = 2
        for i in range(n):
            items.append(WorkItem(name=f"{d}-{i+1}" if n > 1 else d, domain=d))

    # No explicit domain but a real build request → a single generic unit.
    if not domains and _BUILD_RE.search(text):
        domains = ["frontend"] if re.search(r"\b(page|site|ui)\b", text, re.I) else ["backend"]
        items.append(WorkItem(name="task", domain=domains[0]))

    return domains, items


# ── 2. Dependency Graph Engine ───────────────────────────────────────────────

@dataclass
class GraphMetrics:
    nodes: List[str]
    edges: List[Tuple[str, str]]            # (dependency, dependent)
    has_cycle: bool
    longest_path: List[str]
    critical_path_weight: float
    independent_nodes: int                  # max concurrent (DAG width)
    total_nodes: int
    parallelism_ratio: float
    parallelism_class: str
    layers: List[List[str]]

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes, "edges": [list(e) for e in self.edges],
            "has_cycle": self.has_cycle, "longest_path": self.longest_path,
            "critical_path_weight": round(self.critical_path_weight, 1),
            "independent_nodes": self.independent_nodes, "total_nodes": self.total_nodes,
            "parallelism_ratio": round(self.parallelism_ratio, 3),
            "parallelism_class": self.parallelism_class,
            "layers": self.layers,
        }


class DependencyGraph:
    """A true DAG over ownership domains."""

    def __init__(self, domains: List[str], weights: Optional[Dict[str, float]] = None):
        self.nodes = list(dict.fromkeys(domains))           # de-dup, keep order
        self.weights = weights or {n: 1.0 for n in self.nodes}
        present = set(self.nodes)
        self.deps: Dict[str, List[str]] = {
            n: [d for d in _DOMAIN_DEPS.get(n, []) if d in present] for n in self.nodes
        }
        self.edges = [(d, n) for n in self.nodes for d in self.deps[n]]

    # -- structure ----------------------------------------------------------

    def _has_cycle(self) -> bool:
        WHITE, GREY, BLACK = 0, 1, 2
        color = {n: WHITE for n in self.nodes}

        def visit(n) -> bool:
            color[n] = GREY
            for dep in self.deps[n]:
                if color[dep] == GREY:
                    return True
                if color[dep] == WHITE and visit(dep):
                    return True
            color[n] = BLACK
            return False

        return any(color[n] == WHITE and visit(n) for n in self.nodes)

    def _level(self) -> Dict[str, int]:
        """Topological level of each node (longest dependency depth)."""
        memo: Dict[str, int] = {}

        def lvl(n, stack):
            if n in memo:
                return memo[n]
            if n in stack:            # cycle guard
                return 0
            stack.add(n)
            d = self.deps[n]
            v = 0 if not d else 1 + max(lvl(x, stack) for x in d)
            stack.discard(n)
            memo[n] = v
            return v

        return {n: lvl(n, set()) for n in self.nodes}

    def layers(self) -> List[List[str]]:
        level = self._level()
        out: Dict[int, List[str]] = {}
        for n in self.nodes:
            out.setdefault(level[n], []).append(n)
        return [out[k] for k in sorted(out)]

    def longest_path(self) -> Tuple[List[str], float]:
        """Critical path by accumulated node weight."""
        best_len: Dict[str, float] = {}
        best_prev: Dict[str, Optional[str]] = {}

        def dfs(n, stack):
            if n in best_len:
                return best_len[n]
            if n in stack:
                return 0.0
            stack.add(n)
            w = self.weights.get(n, 1.0)
            best, prev = w, None
            for dep in self.deps[n]:
                cand = w + dfs(dep, stack)
                if cand > best:
                    best, prev = cand, dep
            stack.discard(n)
            best_len[n] = best
            best_prev[n] = prev
            return best

        if not self.nodes:
            return [], 0.0
        end = max(self.nodes, key=lambda n: dfs(n, set()))
        path, cur = [], end
        seen = set()
        while cur is not None and cur not in seen:
            path.append(cur); seen.add(cur)
            cur = best_prev.get(cur)
        path.reverse()
        return path, best_len.get(end, 0.0)

    def width(self) -> int:
        """Max concurrency = widest topological layer (max antichain proxy)."""
        ls = self.layers()
        return max((len(l) for l in ls), default=0)

    def parallelism(self) -> Tuple[int, float, str]:
        total = len(self.nodes)
        if total <= 1:
            return total, 0.0, "none"
        w = self.width()
        ratio = w / total
        # Parallelism requires at least 2 concurrent nodes to mean anything.
        if w < 2:
            return w, 0.0, "very_low"
        cls = ("very_high" if ratio >= 0.8 else "high" if ratio >= 0.6
               else "medium" if ratio >= 0.4 else "low" if ratio >= 0.2 else "very_low")
        return w, ratio, cls

    def metrics(self) -> GraphMetrics:
        path, cpw = self.longest_path()
        w, ratio, cls = self.parallelism()
        return GraphMetrics(
            nodes=self.nodes, edges=self.edges, has_cycle=self._has_cycle(),
            longest_path=path, critical_path_weight=cpw,
            independent_nodes=w, total_nodes=len(self.nodes),
            parallelism_ratio=ratio, parallelism_class=cls, layers=self.layers(),
        )


# ── 1. Complexity Engine ─────────────────────────────────────────────────────

@dataclass
class ComplexityBreakdown:
    file_count: int
    loc: int
    components: int
    dependency_depth: int
    coupling: int                  # edges between domains
    cross_module: int              # domains touching >1 other domain
    risk: str
    context_items: int
    score: float                   # 0–100
    level: str                     # low | medium | high
    factors: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "file_count": self.file_count, "loc": self.loc, "components": self.components,
            "dependency_depth": self.dependency_depth, "coupling": self.coupling,
            "cross_module": self.cross_module, "risk": self.risk,
            "context_items": self.context_items, "score": round(self.score, 1),
            "level": self.level, "factors": {k: round(v, 1) for k, v in self.factors.items()},
        }


def analyze_complexity(text: str, domains: List[str], items: List[WorkItem],
                       graph: DependencyGraph) -> ComplexityBreakdown:
    gm = graph.metrics()
    components = len(domains)
    files = max(len(items), len(set(_FILE_EXT_RE.findall(text or ""))) or 0, components)
    loc = sum(i.loc for i in items) or files * _LOC_PER_ITEM
    depth = len(gm.longest_path)
    coupling = len(gm.edges)
    cross = sum(1 for n in domains if len(graph.deps.get(n, [])) +
                sum(1 for e in gm.edges if e[0] == n) > 1)
    risk = "high" if _RISK_HIGH.search(text or "") else ("medium" if components >= 3 else "low")

    factors = {
        "components": min(40.0, components * 12),
        "dependency_depth": min(20.0, depth * 7),
        "files": min(15.0, files * 1.5),
        "coupling": min(12.0, coupling * 4),
        "cross_module": min(8.0, cross * 4),
        "risk": {"low": 0.0, "medium": 6.0, "high": 12.0}[risk],
        "context": min(8.0, len(items) * 1.0),
    }
    score = min(100.0, sum(factors.values()))
    level = "high" if score >= 66 else "medium" if score >= 35 else "low"
    return ComplexityBreakdown(
        file_count=files, loc=loc, components=components, dependency_depth=depth,
        coupling=coupling, cross_module=cross, risk=risk, context_items=len(items),
        score=score, level=level, factors=factors,
    )


# ── 7. ETA Engine ────────────────────────────────────────────────────────────

@dataclass
class ETABreakdown:
    mode: str
    execution_s: float
    context_load_s: float
    agent_startup_s: float
    coordination_s: float
    merge_s: float
    validation_s: float
    total_s: float

    def to_dict(self) -> dict:
        return {k: round(v, 1) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


def estimate_eta(items: List[WorkItem], graph: DependencyGraph, mode: str,
                 agents: int) -> ETABreakdown:
    gm = graph.metrics()
    total_work = sum(i.loc for i in items) / _LOC_PER_ITEM * _SECONDS_PER_ITEM or _SECONDS_PER_ITEM
    n_items = max(1, len(items))
    layers = max(1, len(gm.layers))

    if mode == "direct" or agents <= 1:
        return ETABreakdown("direct", total_work, _CONTEXT_LOAD_S, 0.0, 0.0, 0.0,
                            n_items * _VALIDATE_PER_ITEM_S,
                            total_work + _CONTEXT_LOAD_S + n_items * _VALIDATE_PER_ITEM_S)

    # Parallel: execution bounded by the critical path (work along longest chain),
    # not the total — but never below total/agents (Brent's bound).
    crit = max(gm.critical_path_weight, 1.0) * _SECONDS_PER_ITEM
    parallel_floor = total_work / max(1, min(agents, gm.independent_nodes or 1))
    execution = max(crit, parallel_floor)
    startup = _AGENT_STARTUP_S * agents
    context = _CONTEXT_LOAD_S * max(1, agents) * 0.5
    coordination = _COORD_PER_LAYER_S * layers
    merge = _MERGE_PER_AGENT_S * agents
    validation = n_items * _VALIDATE_PER_ITEM_S
    total = execution + context + startup + coordination + merge + validation
    return ETABreakdown(mode, execution, context, startup, coordination, merge,
                        validation, total)


# ── 8. Cost Model ────────────────────────────────────────────────────────────

@dataclass
class CostBreakdown:
    tokens: int
    context_transfer: int
    communication: int
    merge_complexity: int
    validation_complexity: int
    total_tokens: int
    efficiency: float              # speedup per agent (>1 good, <1 wasteful)

    def to_dict(self) -> dict:
        return {k: round(v, 3) if isinstance(v, float) else v
                for k, v in self.__dict__.items()}


def cost_model(items: List[WorkItem], graph: DependencyGraph, agents: int,
               speedup: float) -> CostBreakdown:
    gm = graph.metrics()
    base = max(1, len(items)) * _TOKENS_PER_ITEM
    xfer = _CONTEXT_XFER_PER_AGENT_TOKENS * max(0, agents - 1)
    comm = _COMM_PER_EDGE_TOKENS * len(gm.edges) * (1 if agents > 1 else 0)
    merge = int(120 * max(0, agents - 1) * (1 + gm.parallelism_ratio))
    validation = int(80 * len(items))
    total = base + xfer + comm + merge + validation
    efficiency = round(speedup / agents, 3) if agents else 0.0
    return CostBreakdown(base, xfer, comm, merge, validation, total, efficiency)


# ── 3. Swarm Decision System ─────────────────────────────────────────────────

@dataclass
class BenefitBreakdown:
    parallel_savings_s: float
    coordination_cost_s: float
    merge_cost_s: float
    communication_cost_s: float
    net_benefit_s: float

    def to_dict(self) -> dict:
        return {k: round(v, 1) for k, v in self.__dict__.items()}


def swarm_benefit(seq_eta: ETABreakdown, par_eta: ETABreakdown,
                  graph: DependencyGraph, agents: int) -> BenefitBreakdown:
    gm = graph.metrics()
    savings = seq_eta.total_s - par_eta.execution_s
    coordination = par_eta.coordination_s
    merge = par_eta.merge_s
    communication = (_COMM_PER_EDGE_TOKENS * len(gm.edges)) / 1000.0  # tokens→~s proxy
    net = savings - coordination - merge - communication
    return BenefitBreakdown(savings, coordination, merge, communication, net)


# ── 4. Agent Allocation Engine ───────────────────────────────────────────────

@dataclass
class AgentAllocation:
    agents: List[dict] = field(default_factory=list)   # [{domain, work_items, files}]

    @property
    def count(self) -> int:
        return len(self.agents)

    def to_dict(self) -> dict:
        return {"count": self.count, "agents": self.agents}


def allocate_agents(domains: List[str], items: List[WorkItem]) -> AgentAllocation:
    """One agent per ownership domain. Sections/pages are work items *within* an
    agent, never their own agent (the anti-inflation rule)."""
    by_domain: Dict[str, List[str]] = {}
    for it in items:
        by_domain.setdefault(it.domain, []).append(it.name)
    for d in domains:
        by_domain.setdefault(d, [])
    agents = [{"domain": d, "work_items": by_domain[d], "files": max(1, len(by_domain[d]))}
              for d in by_domain]
    return AgentAllocation(agents=agents)


# ── 6. Execution Mode Selector ───────────────────────────────────────────────

def select_mode(complexity: ComplexityBreakdown, graph: DependencyGraph,
                benefit: BenefitBreakdown) -> Tuple[str, str]:
    gm = graph.metrics()
    units = gm.total_nodes
    width = gm.independent_nodes

    if units <= 1:
        return "direct", f"single ownership domain ({units} unit) — one agent, no orchestration"
    if units <= 3 and complexity.level != "high":
        return ("direct",
                f"{units} small domains, {complexity.level} complexity — orchestration overhead not justified")
    # Swarm only when wide parallelism AND positive net benefit (real savings).
    if width >= 3 and gm.parallelism_class in ("high", "very_high") and benefit.net_benefit_s > 0:
        return ("swarm",
                f"{width} independent branches, {gm.parallelism_class} parallelism, "
                f"net benefit {benefit.net_benefit_s:.0f}s — swarm")
    # Otherwise multi-domain work with dependencies → DAG.
    return ("dag",
            f"{units} domains with dependencies (depth {len(gm.longest_path)}, "
            f"parallelism {gm.parallelism_class}) — staged orchestration")


# ── 9. Orchestration Validation Layer ────────────────────────────────────────

@dataclass
class ValidationResult:
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings}


def validate_plan(plan: "ExecutionPlan") -> ValidationResult:
    errors, warnings = [], []
    gm = plan.graph
    # Agent allocation rules:
    #  • orchestrated modes → exactly one agent per domain node (no inflation)
    #  • direct → a single agent regardless of node count (intentional)
    #  • clarify → no agents
    if plan.mode in ("dag", "swarm"):
        if plan.allocation.count != gm.total_nodes and gm.total_nodes > 0:
            errors.append(f"agent count {plan.allocation.count} != domain nodes {gm.total_nodes} (inflation)")
    elif plan.mode == "direct" and plan.allocation.count > 1:
        errors.append(f"direct mode with {plan.allocation.count} agents")
    elif plan.mode == "clarify" and plan.allocation.count != 0:
        errors.append("clarify mode must spawn no agents")
    # Graph integrity.
    if gm.has_cycle:
        errors.append("dependency graph contains a cycle")
    # ETA consistency.
    if plan.mode != "direct" and plan.par_eta.total_s <= 0:
        errors.append("non-direct mode with non-positive ETA")
    if plan.speedup < 0:
        errors.append("negative speedup")
    # Swarm must be justified — by graph parallelism, net benefit, OR scale/stream
    # fan-out (where each domain becomes a parallel team).
    if (plan.mode == "swarm" and plan.benefit.net_benefit_s <= 0
            and gm.independent_nodes < 3 and not plan.scale_justified):
        errors.append("swarm selected but not justified (no parallel benefit, narrow graph, no scale)")
    # Parallelism honesty.
    if gm.parallelism_class in ("high", "very_high") and gm.independent_nodes < 3:
        warnings.append("parallelism class high but <3 independent nodes")
    if plan.mode == "swarm" and gm.total_nodes < 4:
        warnings.append("swarm with few nodes — consider DAG")
    return ValidationResult(valid=not errors, errors=errors, warnings=warnings)


# ── Plan + entry point ───────────────────────────────────────────────────────

@dataclass
class ExecutionPlan:
    mode: str
    reasoning: str
    complexity: ComplexityBreakdown
    graph: GraphMetrics
    allocation: AgentAllocation
    seq_eta: ETABreakdown
    par_eta: ETABreakdown
    benefit: BenefitBreakdown
    cost: CostBreakdown
    speedup: float
    confidence: float
    scale_justified: bool = False        # swarm justified by scale/stream fan-out
    validation: ValidationResult = None  # set after construction

    def to_dict(self) -> dict:
        return {
            "mode": self.mode, "reasoning": self.reasoning, "speedup": round(self.speedup, 2),
            "confidence": round(self.confidence, 2),
            "complexity": self.complexity.to_dict(), "graph": self.graph.to_dict(),
            "allocation": self.allocation.to_dict(),
            "seq_eta": self.seq_eta.to_dict(), "par_eta": self.par_eta.to_dict(),
            "benefit": self.benefit.to_dict(), "cost": self.cost.to_dict(),
            "validation": self.validation.to_dict() if self.validation else None,
        }


def _confidence(complexity: ComplexityBreakdown, gm: GraphMetrics) -> float:
    """Higher when signals are strong & consistent; lower for tiny/ambiguous tasks."""
    base = 0.5
    if complexity.components >= 2:
        base += 0.2
    if gm.total_nodes >= 1 and not gm.has_cycle:
        base += 0.15
    if complexity.file_count >= 3:
        base += 0.1
    return round(min(0.95, base), 2)


def _clarify_plan(text: str) -> "ExecutionPlan":
    """A non-executable plan for ambiguous requests — clarification required."""
    g = DependencyGraph([])
    empty_eta = ETABreakdown("clarify", 0, 0, 0, 0, 0, 0, 0)
    plan = ExecutionPlan(
        mode="clarify",
        reasoning="ambiguous request — no concrete target; clarification required before execution",
        complexity=analyze_complexity(text, [], [], g), graph=g.metrics(),
        allocation=AgentAllocation(agents=[]),
        seq_eta=empty_eta, par_eta=empty_eta,
        benefit=BenefitBreakdown(0, 0, 0, 0, 0),
        cost=CostBreakdown(0, 0, 0, 0, 0, 0, 0.0),
        speedup=1.0, confidence=0.2,
    )
    plan.validation = ValidationResult(valid=True, warnings=["clarification required"])
    return plan


def optimize(text: str) -> ExecutionPlan:
    """Full graph-driven analysis → reasoned ExecutionPlan."""
    text = text or ""
    if is_ambiguous(text):
        return _clarify_plan(text)

    # Content/work fan-out (≥3 independent streams) → genuinely parallel → swarm.
    streams = detect_streams(text)
    domains, items = parse_work(text)
    stream_mode = len(streams) >= 3 and len(streams) >= len(domains)
    if stream_mode:
        domains = list(streams)                 # each stream is an independent node
        items = [WorkItem(name=s, domain=s) for s in streams]

    graph = DependencyGraph(domains, weights={
        d: max(1.0, sum(1 for it in items if it.domain == d)) for d in domains})
    gm = graph.metrics()
    complexity = analyze_complexity(text, domains, items, graph)
    allocation = allocate_agents(domains, items)
    agents = max(1, allocation.count)

    seq_eta = estimate_eta(items, graph, "direct", 1)
    par_eta = estimate_eta(items, graph, "dag", agents)
    benefit = swarm_benefit(seq_eta, par_eta, graph, agents)
    # Speedup reflects compute parallelism (critical-path bound), not overhead —
    # a pure chain → ~1.0, a wide graph → >1.0. Never the misleading <1 from
    # overhead alone.
    speedup = seq_eta.execution_s / par_eta.execution_s if par_eta.execution_s > 0 else 1.0

    mode, reasoning = select_mode(complexity, graph, benefit)
    scaled = bool(_SCALE.search(text))
    if stream_mode and gm.independent_nodes >= 3:
        mode, reasoning = "swarm", (f"{len(streams)} independent work streams "
                                    f"({', '.join(streams[:6])}) — fully parallel; swarm")
    elif scaled and mode == "dag" and gm.total_nodes >= 4:
        mode, reasoning = "swarm", (f"scale signal + {gm.total_nodes} domains — each becomes a "
                                    f"parallel team; swarm")

    chosen_eta = seq_eta if mode == "direct" else par_eta
    if mode == "direct":
        speedup = 1.0
    cost = cost_model(items, graph, agents, speedup)

    plan = ExecutionPlan(
        mode=mode, reasoning=reasoning, complexity=complexity, graph=gm,
        allocation=(AgentAllocation(agents=allocation.agents[:1]) if mode == "direct"
                    else allocation),
        seq_eta=seq_eta, par_eta=chosen_eta, benefit=benefit, cost=cost,
        speedup=speedup, confidence=_confidence(complexity, gm),
        scale_justified=bool(stream_mode or scaled),
    )
    plan.validation = validate_plan(plan)
    return plan


# ── 10. Reporting ────────────────────────────────────────────────────────────

def render_report(plan: ExecutionPlan) -> str:
    c, g = plan.complexity, plan.graph
    lines = [
        "Orchestration Analysis",
        "",
        "Complexity Analysis",
        f"  Score {c.score:.0f}/100 ({c.level})  · components {c.components} · "
        f"files {c.file_count} · LOC ~{c.loc} · depth {c.dependency_depth} · risk {c.risk}",
        "  Factors: " + ", ".join(f"{k} {v:.0f}" for k, v in c.factors.items()),
        "",
        "Dependency Analysis",
        f"  Nodes {g.total_nodes} · edges {len(g.edges)} · critical path "
        f"{' → '.join(g.longest_path) or '—'} · cycle {g.has_cycle}",
        "",
        "Parallelism Analysis",
        f"  Independent (max concurrent) {g.independent_nodes}/{g.total_nodes} · "
        f"ratio {g.parallelism_ratio:.2f} · {g.parallelism_class.upper()}",
        "",
        "Execution Strategy",
        f"  Mode {plan.mode.upper()} — {plan.reasoning}",
        f"  Sequential ETA {plan.seq_eta.total_s:.0f}s · Chosen ETA {plan.par_eta.total_s:.0f}s · "
        f"speedup {plan.speedup:.2f}x · confidence {plan.confidence:.0%}",
        "",
        "Agent Allocation (one per ownership domain)",
    ]
    for a in plan.allocation.agents:
        lines.append(f"  • {a['domain']} agent — {len(a['work_items'])} work items")
    lines += [
        "",
        "Swarm Benefit",
        f"  savings {plan.benefit.parallel_savings_s:.0f}s − coord {plan.benefit.coordination_cost_s:.0f}s "
        f"− merge {plan.benefit.merge_cost_s:.0f}s − comm {plan.benefit.communication_cost_s:.0f}s "
        f"= net {plan.benefit.net_benefit_s:.0f}s",
        "",
        "Cost Model",
        f"  ~{plan.cost.total_tokens:,} tokens · efficiency {plan.cost.efficiency:.2f} speedup/agent",
        "",
        f"Validation: {'PASS' if plan.validation.valid else 'REJECT'}"
        + ("" if plan.validation.valid else "  — " + "; ".join(plan.validation.errors)),
    ]
    return "\n".join(lines)
