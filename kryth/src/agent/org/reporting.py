"""Enterprise reporting (Phase L).

Generates executive / portfolio / department / release / risk / budget /
quarterly reports from the org models, exportable as Markdown, JSON, or a dict.
Pure read-only rendering — it never mutates the models it reports on.

Each builder takes the relevant model object(s) and returns a dict; `to_markdown`
/ `to_json` render any report dict uniformly.
"""

from __future__ import annotations

import json
from typing import Optional


def executive_report(*, portfolio=None, departments=None, releases=None,
                     budgets=None) -> dict:
    rep: dict = {"report": "executive"}
    if portfolio is not None:
        rep["portfolio_health"] = portfolio.health()
    if departments is not None:
        ranked = departments.rank_by_reputation()
        rep["top_department"] = ranked[0].name if ranked else None
    if releases is not None:
        rep["releases"] = releases.metrics()
    if budgets is not None:
        rep["budget_warnings"] = budgets.warnings()
    return rep


def portfolio_report(portfolio) -> dict:
    return {"report": "portfolio", "health": portfolio.health(),
            "missions": portfolio.snapshot()}


def department_report(departments) -> dict:
    return {"report": "department", "departments": departments.snapshot()}


def release_report(releases) -> dict:
    return {"report": "release", "metrics": releases.metrics(),
            "history": releases.history()}


def risk_report(*, portfolio=None, budgets=None, bottleneck: Optional[dict] = None) -> dict:
    risks = []
    if portfolio is not None:
        h = portfolio.health()
        if h["risk_level"] != "low":
            risks.append(f"Portfolio risk {h['risk_level'].upper()} "
                         f"({h['high_risk_missions']} high-risk missions)")
        if h["blocked"]:
            risks.append(f"{h['blocked']} missions blocked")
    if budgets is not None:
        for w in budgets.warnings():
            risks.append(f"Budget {w['dimension']}:{w['name']} {w['status']} ({w['usage']:.0%})")
    if bottleneck and bottleneck.get("dependency"):
        risks.append(f"Bottleneck: {bottleneck['dependency']} blocking "
                     f"{bottleneck.get('blocking', 0)} tasks ({bottleneck.get('impact', 'low')})")
    level = "high" if any("HIGH" in r or "exhausted" in r for r in risks) else (
        "medium" if risks else "low")
    return {"report": "risk", "risk_level": level, "risks": risks}


def budget_report(budgets) -> dict:
    return {"report": "budget", **budgets.report()}


def quarterly_report(roadmap, quarter: str) -> dict:
    return {"report": "quarterly", "quarter": quarter,
            "capacity": roadmap.capacity_plan(quarter),
            "items": [r for r in roadmap.tree() if r["quarter"] == quarter]}


# -- rendering ----------------------------------------------------------------

def to_json(report: dict, *, indent: int = 2) -> str:
    return json.dumps(report, indent=indent, default=str)


def to_markdown(report: dict) -> str:
    name = str(report.get("report", "report")).title()
    lines = [f"# KRYTH {name} Report", ""]

    def render(value, depth=0):
        pad = "  " * depth
        if isinstance(value, dict):
            for k, v in value.items():
                if k == "report":
                    continue
                if isinstance(v, (dict, list)):
                    lines.append(f"{pad}- **{k}**:")
                    render(v, depth + 1)
                else:
                    lines.append(f"{pad}- {k}: {v}")
        elif isinstance(value, list):
            for item in value[:50]:
                if isinstance(item, dict):
                    label = item.get("id") or item.get("name") or item.get("dependency") or "item"
                    lines.append(f"{pad}- {label}: "
                                 + ", ".join(f"{k}={v}" for k, v in item.items()
                                             if k not in ("id", "name") and not isinstance(v, (dict, list)))[:160])
                else:
                    lines.append(f"{pad}- {item}")
        else:
            lines.append(f"{pad}- {value}")

    render(report)
    return "\n".join(lines)
