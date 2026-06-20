"""Universal Action Layer — public package exports.

The UAL is the kernel of KRYTH. Every mission passes through here:

  Mission → ActionGraph → ActionScheduler → ExecutorSelector
         → Executor → ActionVerifier → ActionMemory → ActionAnalytics

Quick start::

    from agent.action import ActionManager

    manager = ActionManager()
    result = manager.run_plan({
        "mission": "Deploy my application",
        "actions": [
            {
                "id": "build",
                "goal": "Build backend",
                "priority": 10,
                "preferred_executors": ["terminal"],
                "params": {"command": "make build"},
                "verification_steps": ["check_exit_0"],
                "rollback_steps": ["run:make clean"],
                "dependencies": []
            },
            {
                "id": "deploy",
                "goal": "Deploy via API",
                "priority": 20,
                "preferred_executors": ["api"],
                "params": {"url": "https://deploy.example.com", "method": "POST"},
                "dependencies": ["build"]
            }
        ]
    })
    print(result.success, result.succeeded, result.failed)

Plugin executor example::

    from agent.action import ActionManager, BaseExecutor, ActionResult

    class MyCloudExecutor(BaseExecutor):
        @property
        def name(self): return "my_cloud"
        @property
        def capabilities(self): return ["deploy to MyCloud"]
        def can_handle(self, action): return "my_cloud" in action.preferred_executors
        def estimate_confidence(self, action): return 0.9
        def estimate_speed_ms(self, action): return 5000
        def execute(self, action): ...

    manager = ActionManager()
    manager.register_executor(MyCloudExecutor())
"""

from agent.action.action import (
    Action,
    ActionResult,
    ActionStatus,
    ActionRisk,
)
from agent.action.action_graph import ActionGraph
from agent.action.action_manager import ActionManager
from agent.action.action_registry import (
    ActionRegistry,
    BaseExecutor,
    TerminalExecutor,
    BrowserExecutor,
    APIExecutor,
    DesktopExecutor,
    CloudExecutor,
    get_registry,
)
from agent.action.action_scheduler import ActionScheduler, SchedulerResult
from agent.action.action_verifier import ActionVerifier
from agent.action.rollback_engine import RollbackEngine
from agent.action.action_memory import ActionMemory
from agent.action.action_analytics import ActionAnalytics
from agent.action.executor_selector import ExecutorSelector, ExecutorScore
from agent.action.action_dashboard import push_action_event, render_ual_panel

__all__ = [
    # Core action object
    "Action",
    "ActionResult",
    "ActionStatus",
    "ActionRisk",
    # Graph
    "ActionGraph",
    # Entry point
    "ActionManager",
    # Registry + executors
    "ActionRegistry",
    "BaseExecutor",
    "TerminalExecutor",
    "BrowserExecutor",
    "APIExecutor",
    "DesktopExecutor",
    "CloudExecutor",
    "get_registry",
    # Scheduler
    "ActionScheduler",
    "SchedulerResult",
    # Subsystems
    "ActionVerifier",
    "RollbackEngine",
    "ActionMemory",
    "ActionAnalytics",
    "ExecutorSelector",
    "ExecutorScore",
    # Dashboard
    "push_action_event",
    "render_ual_panel",
]
