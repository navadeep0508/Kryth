"""Phase O deliverable — enterprise readiness audit.
Run:  python tests/enterprise_readiness_audit.py

Exercises the full org layer end-to-end (portfolio → departments → releases →
budgets → analytics → reporting) and prints the maturity audit + overall score.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.org.portfolio import PortfolioManager, MissionState
from agent.org.departments import DepartmentRegistry
from agent.org.release import ReleasePipeline
from agent.org.budget import BudgetManager
from agent.org.analytics import organization_summary
from agent.org.reporting import executive_report, to_markdown
from agent.org.readiness_audit import MaturityInputs, audit_report


def main() -> int:
    # Build a small live organization.
    pm = PortfolioManager()
    pm.add_mission("saas", title="SaaS Platform", priority=1, owner="backend", budget_tokens=120000)
    pm.add_mission("docs", title="Documentation", priority=3, owner="documentation", budget_tokens=20000)
    pm.set_state("saas", MissionState.RUNNING); pm.update("saas", progress=82, risk="medium", eta_min=8)
    pm.update("docs", progress=100)

    reg = DepartmentRegistry(autoload=False)
    for d, ok in [("backend", True), ("backend", True), ("frontend", True), ("testing", False)]:
        reg.record_mission(d, success=ok)
    reg.assign("backend", 4); reg.assign("frontend", 2)

    rp = ReleasePipeline()
    rp.create("v1", now=0)
    for t in (1, 2, 3, 4):
        rp.advance("v1", now=t, confidence=0.9)

    bm = BudgetManager()
    bm.set_budget("token", "saas", 120000); bm.spend("token", "saas", 60000)

    print(to_markdown(executive_report(portfolio=pm, departments=reg, releases=rp, budgets=bm)))
    print()
    summary = organization_summary(portfolio=pm, departments=reg)
    print("Organization analytics:")
    print(f"  top department: {summary['top_department']['name'] if summary.get('top_department') else '—'}")
    print(f"  portfolio success rate: {summary['portfolio']['success_rate']:.0%}")
    print()

    # Maturity inputs derived from what we measured + known gate status.
    inp = MaturityInputs(
        architecture=92, operational=70, organizational=80,
        release=rp.metrics()["deployment_success_rate"] * 100,
        portfolio=85, department=reg.rank_by_reputation()[0].reputation * 100,
        budget=65,
    )
    print(audit_report(inp))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
