"""Organizational analytics (Phase I).

Derives leadership/department/mission/portfolio rankings from the org models:
top-performing department, most-productive lead, most-reliable team, highest
success rate, most-expensive mission, best-ROI mission. Pure read-only analysis
over the structures the other modules already track.
"""

from __future__ import annotations

from typing import List, Optional


def _safe_max(items, key):
    items = list(items)
    return max(items, key=key) if items else None


def top_department(registry) -> Optional[dict]:
    """Department with the best reputation (ties → most missions)."""
    ranked = registry.rank_by_reputation()
    return ranked[0].to_dict() if ranked else None


def department_utilization(registry) -> List[dict]:
    rows = registry.snapshot()
    return sorted(rows, key=lambda d: d["utilization"], reverse=True)


def most_productive_lead(hierarchy) -> Optional[dict]:
    leads = [m for m in hierarchy.chart() if m["role"] == "lead"]
    if not leads:
        return None
    best = _safe_max(leads, key=lambda m: hierarchy.team_productivity(m["id"]))
    if not best:
        return None
    return {"lead": best["id"], "name": best["name"],
            "team_productivity": hierarchy.team_productivity(best["id"]),
            "team_utilization": hierarchy.team_utilization(best["id"])}


def most_reliable_team(hierarchy) -> Optional[dict]:
    leads = [m for m in hierarchy.chart() if m["role"] == "lead"]
    scored = [(m["id"], hierarchy.team_productivity(m["id"])) for m in leads]
    scored = [s for s in scored if s[1] > 0]
    if not scored:
        return None
    lead_id, prod = max(scored, key=lambda s: s[1])
    return {"lead": lead_id, "reliability": prod}


def portfolio_analytics(portfolio) -> dict:
    """Highest success rate / most-expensive / best-ROI across missions.

    ROI proxy = progress per 1k budget tokens (value delivered per cost)."""
    missions = portfolio.snapshot()
    done = [m for m in missions if m["state"] in ("completed", "failed")]
    completed = [m for m in done if m["state"] == "completed"]
    success_rate = round(len(completed) / len(done), 3) if done else 0.0

    most_expensive = _safe_max(missions, key=lambda m: m["budget_tokens"])

    def _roi(m):
        return m["progress"] / (m["budget_tokens"] / 1000) if m["budget_tokens"] else m["progress"]
    best_roi = _safe_max([m for m in missions if m["budget_tokens"]], key=_roi)

    return {
        "success_rate": success_rate,
        "most_expensive_mission": most_expensive["id"] if most_expensive else None,
        "most_expensive_tokens": most_expensive["budget_tokens"] if most_expensive else 0,
        "best_roi_mission": best_roi["id"] if best_roi else None,
        "best_roi": round(_roi(best_roi), 2) if best_roi else 0.0,
    }


def organization_summary(*, portfolio=None, departments=None, hierarchy=None) -> dict:
    """One-call rollup for the operations center / enterprise report."""
    out: dict = {}
    if portfolio is not None:
        out["portfolio"] = portfolio_analytics(portfolio)
        out["portfolio_health"] = portfolio.health()
    if departments is not None:
        out["top_department"] = top_department(departments)
        out["department_utilization"] = department_utilization(departments)
    if hierarchy is not None:
        out["most_productive_lead"] = most_productive_lead(hierarchy)
        out["most_reliable_team"] = most_reliable_team(hierarchy)
    return out
