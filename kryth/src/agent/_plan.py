"""Plan-mode exit tool."""

from __future__ import annotations

from agent import ui
from agent.tools._results import err


def exit_plan_mode(plan):
    from agent.session import get_session
    from agent.io import confirm

    if not isinstance(plan, str) or not plan.strip():
        return err("BAD_ARGS", "plan must be a non-empty string")

    ui.plan_prose(plan)

    session = get_session()
    if confirm("\nApprove plan and exit plan mode?", default=False):
        session.mode = "default"
        return "Plan approved. You may now make changes."
    return "Plan rejected. Refine and call exit_plan_mode again, or stop."
