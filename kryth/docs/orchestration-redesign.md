# Orchestration Engine — Architectural Review & Redesign

A complete review and refactor of complexity analysis, DAG planning, the swarm
decision engine, agent allocation, dependency analysis, ETA estimation, and
execution-mode selection. The engine moves from a **heuristic/keyword scorer**
to a **graph-driven execution optimizer** that chooses Direct / DAG / Swarm by
measurable execution benefit.

Implementation: `src/agent/orchestration_optimizer.py` (the engine);
`src/agent/mission_estimator.py` delegates to it. Tests:
`tests/test_orchestration_optimizer.py`, `tests/test_orchestration_validation_suite.py`.

---

## 1. Flaws in the previous implementation (root cause)

| Flaw | Root cause |
|---|---|
| **Agent inflation** — "Features Agent, Contact Agent, Team Agent…" | Parallelism was derived from counting **sections/pages**. Each section became an "independent unit" → one agent each. |
| **Swarm over-selection** ("12 streams → SWARM") | `independent_units = components + sections`; sections inflated the count past the swarm threshold. |
| **Parallelism inflation** | Reported HIGH/VERY_HIGH from raw counts with **no graph** — never checked whether units could actually run concurrently. |
| **Dependency chains ignored** | There was no dependency graph; `database → backend → frontend` was treated as independent parallel work. |
| **Keyword complexity inflation** | Complexity was keyword score; "login page" with the words auth/db/api scored 100/100. |
| **Inconsistent ETA** | Speedup mixed coordination overhead into the ratio → DAG could report `0.5x` (slower than sequential) yet still be chosen. |
| **Plan ≠ agent graph** | The execution preview (sections) did not match the agents actually spawned (domains). |

**Single root cause:** the unit of parallelism was the *artifact* (page/section),
not the *owner* (domain). Sections of one page are one person's job.

---

## 2. New architecture (graph-driven)

```
text → parse_work() ──► domains + work-items   (sections collapse into frontend)
            │
            ├─ ambiguity?  → CLARIFY (no execution)
            ├─ ≥3 content streams? → independent stream nodes
            │
            ▼
   DependencyGraph(domains)         nodes = OWNERSHIP DOMAINS, edges = canonical deps
            │
   ┌────────┼─────────────────────────────────────────────┐
   ▼        ▼              ▼              ▼                  ▼
complexity  graph metrics  agent alloc   ETA (per mode)     swarm benefit
(explainable) (cycle/      (1 per domain) (exec+ctx+startup  (savings − coord
            critical path/               +coord+merge+valid) − merge − comm)
            width/ratio)
            │
            ▼
       select_mode()  →  Direct / DAG / Swarm  (+ reasoning)
            │
            ▼
       validate_plan()  →  reject inflation / cycles / unjustified swarm
            │
            ▼
        ExecutionPlan  →  render_report()
```

**Key invariant:** `agents == domain nodes` for orchestrated modes (Direct uses 1).
Parallelism = **DAG width** (max concurrent domains), never section count.

---

## 3. Subsystems

1. **Complexity Engine** — `analyze_complexity()` → weighted, explainable score
   from components, dependency depth, files, coupling (edges), cross-module,
   risk, context. Returns a factor breakdown. No keyword inflation.
2. **Dependency Graph Engine** — `DependencyGraph`: canonical domain deps
   (`database→backend→frontend`, `auth→database`, `payments→backend`, …); cycle
   detection (3-colour DFS), topological layers, longest/critical path,
   width (max antichain proxy), parallelism ratio + class.
3. **Swarm Decision** — `swarm_benefit()` = parallel_savings − coordination −
   merge − communication. Swarm only when width ≥ 3 **and** net benefit > 0, or
   scale/stream-justified.
4. **Agent Allocation** — `allocate_agents()`: exactly one agent per ownership
   domain; sections/pages are work-items *within* an agent.
5. **Parallelism Analysis** — `DependencyGraph.parallelism()` → ratio = width /
   nodes, classified Very Low … Very High; requires width ≥ 2 to exceed Very Low.
6. **Execution Mode Selector** — `select_mode()`: Direct (≤3 units, not high
   complexity), DAG (multi-domain with dependencies), Swarm (wide + benefit, or
   scale/stream fan-out). Every decision returns a reason.
7. **ETA Engine** — `estimate_eta()`: execution + context load + agent startup +
   coordination + merge + validation; sequential vs parallel; speedup is
   compute-bound (critical-path), so a pure chain is ~1.0x (never a misleading
   <1x), plus a confidence score.
8. **Cost Model** — `cost_model()`: tokens, context transfer, communication,
   merge, validation, and `efficiency = speedup / agents` (marginal-agent value).
9. **Validation Layer** — `validate_plan()`: rejects agent inflation, cycles,
   inconsistent ETA, and unjustified swarm; warns on dishonest parallelism.
10. **Reporting** — `render_report()`: complexity, dependency, parallelism,
    strategy, allocation, swarm benefit, cost, validation.

---

## 4. Data structures

`WorkItem`, `GraphMetrics`, `ComplexityBreakdown`, `ETABreakdown`,
`CostBreakdown`, `BenefitBreakdown`, `AgentAllocation`, `ValidationResult`,
`ExecutionPlan` — all dataclasses with `to_dict()` for explainability.

---

## 5. Edge cases handled

- **Ambiguous** ("improve application", "make it better") → `clarify` mode, no agents.
- **Sections/pages** ("hero, features, pricing, footer" / "Home, About, Contact") → 1 frontend agent.
- **Cycles** (A→B→C→A) → detected, plan rejected.
- **Huge but sequential** → DAG, not Swarm (chain has width 1).
- **Small with many keywords** ("build a login page") → complexity stays medium.
- **Content fan-out** (100 blogs/tweets/…; research/citation/summary) → Swarm, one agent per stream type (not per item).
- **Scale** (Notion-scale, 500k LOC monorepo, enterprise) → Swarm (each domain a team).
- **Infra group** (k8s/CI-CD/monitoring/logging/alerting) → one infrastructure agent.

---

## 6. Test & benchmark methodology

- **Unit contract**: `tests/test_orchestration_optimizer.py` — each subsystem.
- **Acceptance suite**: `tests/test_orchestration_validation_suite.py` — the 20
  validation cases + edge cases, with a dimensional score (mode / allocation /
  validity ≥ 90–95 %).
- **Benchmark**: `tests/orchestration_optimizer_benchmark.py` — old
  section-counting vs new domain-grouping; headline metric = agents eliminated
  by domain grouping (e.g. landing page 10 → 1).
- **Regression**: full suite + adversarial gate + frozen DAG-scheduler/conversation
  contracts must stay green.
