"""KRYTH organizational layer — the "autonomous engineering organization".

V1.5 organizational & operational completion. Every module here is **additive,
pure, and advisory**: it models the organization (portfolio, roadmap, releases,
departments, hierarchy, communication, budgets) and reports/recommends on it. It
does NOT add planners/memory/executive/dashboards, does NOT replace existing
architecture, and does NOT touch execution, routing, or orchestration.

Modules
-------
portfolio      — concurrent missions: priority, groups, deps, scheduling, budget
roadmap        — Portfolio → Program → Epic → Mission → Task hierarchy + quarters
release         — dev → verify → staging → canary → production → rollback pipeline
departments    — persistent departments with reputation/capacity/utilization
hierarchy      — Department Head → Team Lead → Worker org chart
communication  — status/blocker/escalation/decision logs, stored + replayable
budget         — multi-dimension budgets with forecasting + enforcement
analytics      — department/leadership/mission/portfolio rankings
knowledge      — completed missions → patterns/templates/playbooks
strategy       — organizational recommendations (never auto-executed)
reporting       — executive/portfolio/department/release/risk/budget/quarterly
readiness_audit — maturity dimensions + overall enterprise readiness score
"""
