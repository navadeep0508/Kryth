"""Mission Operating System — public package exports.

The MOS manages a portfolio of concurrent autonomous missions.
It coordinates Planner + UAL + Executive per mission without replacing any layer.

System hierarchy::

    MissionOS            ← portfolio manager (this package)
         |
    MissionScheduler     ← priority-based admission
         |
    MissionRouter        ← maps goal → execution strategy
         |
    Mission runner:
         |
    UniversalPlanner     ← goal → ActionGraph
         |
    ActionManager (UAL)  ← executes ActionGraph
         |
    Executive            ← supervises quality + risk (NEVER bypassed)

Safety constraints:
- MOS MUST NOT replace the Executive Layer.
- MOS MUST NOT execute actions directly.
- MOS MUST NOT modify production without Executive approval.
- MOS MUST NOT bypass UAL.

Quick start::

    from agent.mos import MissionOS

    mos = MissionOS(max_concurrent=4)

    m1 = mos.submit("Build SaaS authentication system", priority="high")
    m2 = mos.submit("Deploy to staging", priority="normal", depends_on=[m1])

    result = mos.run_until_done(timeout_s=3600)
    print(result)
    # {'total': 2, 'running': 0, 'completed': 2, ...}

    report_path = mos.portfolio_report()
"""

from agent.mos.mission_os          import MissionOS
from agent.mos.mission_state       import (
    Mission, MissionStatus, MissionPriorityLevel,
    ResourceBudget, MissionMetrics,
    priority_weight,
)
from agent.mos.mission_priority    import MissionPriority
from agent.mos.mission_queue       import MissionQueue
from agent.mos.mission_allocator   import MissionAllocator
from agent.mos.mission_scheduler   import MissionScheduler
from agent.mos.mission_dependencies import MissionDependencies
from agent.mos.mission_lifecycle   import MissionLifecycle
from agent.mos.mission_router      import MissionRouter, RoutingDecision
from agent.mos.mission_snapshot    import MissionSnapshot
from agent.mos.mission_recovery    import MissionRecovery
from agent.mos.mission_memory      import MissionMemory, MissionRecord
from agent.mos.mission_analytics   import MissionAnalytics, PortfolioMetric
from agent.mos.mission_dashboard   import (
    push_portfolio, render_portfolio_panel, get_portfolio,
)
from agent.mos.portfolio_report    import PortfolioReport
from agent.mos.resource_manager    import ResourceManager, SystemResources

__all__ = [
    # Entry point
    "MissionOS",
    # State
    "Mission",
    "MissionStatus",
    "MissionPriorityLevel",
    "ResourceBudget",
    "MissionMetrics",
    "priority_weight",
    # Scheduling
    "MissionPriority",
    "MissionQueue",
    "MissionScheduler",
    "MissionAllocator",
    "MissionDependencies",
    "MissionLifecycle",
    # Routing
    "MissionRouter",
    "RoutingDecision",
    # Persistence
    "MissionSnapshot",
    "MissionRecovery",
    "MissionMemory",
    "MissionRecord",
    # Analytics
    "MissionAnalytics",
    "PortfolioMetric",
    # Dashboard
    "push_portfolio",
    "render_portfolio_panel",
    "get_portfolio",
    # Reports
    "PortfolioReport",
    # Resources
    "ResourceManager",
    "SystemResources",
]
