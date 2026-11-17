import uuid
from typing import Any

from config.config import Config
from tools.base import Tool, ToolInvocation, ToolKind, ToolResult
from tools.base import ExecutionStatus
from utils.development_supervisor import DevelopmentSupervisor
from pydantic import BaseModel, Field

PENDING = 'pending'
COMPLETED = 'completed'


class PlanParams(BaseModel):

    action: str = Field(
        ...,
        description="Action: 'add', 'complete', 'fail', 'list', 'clear'. Complete requires verification evidence.",
    )

    id: str | None = Field(
        None,
        description="A plan step ID (required for the 'complete' action)",
    )

    content: str | None = Field(
        None,
        description="The content for the 'add' action",
    )
    verification: str | None = Field(None, description="Observed verification evidence required to complete a step.")
    passed: bool = True
    recovery: str | None = Field(None, description="Recovery instructions when a step fails.")


class PlanTool(Tool):
    name = 'plan'
    description = (
        'Manage the plan (a.k.a. todo list) for the current session. Use this to '
        'track progress on complex, multi-step tasks. When the user asks you to '
        'create or update a todo / todos, use this tool. Actions: add, complete, '
        'list, clear.'
    )
    kind = ToolKind.MEMORY
    schema = PlanParams

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._steps: dict[str, str] = {}
        self._status: dict[str, str] = {}
        self.supervisor = DevelopmentSupervisor(config.cwd)
        self._restore()

    def _restore(self) -> None:
        task = self.supervisor.state.get("task_checkpoint") or {}
        self._steps = {step["id"]: step["description"] for step in task.get("steps", [])}
        self._status = {step["id"]: step["status"] for step in task.get("steps", [])}

    def is_mutating(self, params: dict[str, Any]) -> bool:
        return False

    def reset(self) -> None:
        """Reload an unfinished checkpoint instead of discarding interrupted work."""
        self.supervisor = DevelopmentSupervisor(self.config.cwd)
        self._restore()
        task = self.supervisor.state.get("task_checkpoint") or {}
        if task.get("status") != "in_progress":
            self._steps.clear()
            self._status.clear()

    def _snapshot(self) -> dict[str, Any]:
        steps = [
            {'id': step_id, 'content': content, 'status': self._status[step_id]}
            for step_id, content in self._steps.items()
        ]
        return {
            'steps': steps,
            'completed': sum(1 for step in steps if step['status'] == COMPLETED),
            'total': len(steps),
            'task_id': (self.supervisor.state.get("task_checkpoint") or {}).get("task_id"),
            'task_status': (self.supervisor.state.get("task_checkpoint") or {}).get("status", "idle"),
            'pending': sum(1 for step in steps if step['status'] == PENDING),
            'failed': sum(1 for step in steps if step['status'] == "failed"),
        }

    def _listing(self) -> str:
        if not self._steps:
            return "No plan steps."

        snapshot = self._snapshot()
        lines = [f"Plan ({snapshot['completed']}/{snapshot['total']} verified):"]

        for step in snapshot['steps']:
            marker = {'completed': 'x', 'failed': '!', 'pending': ' '}.get(step['status'], '~')
            lines.append(f"  [{marker}] [{step['id']}] {step['content']}")

        return "\n".join(lines)

    async def execute(self, invocation: ToolInvocation) -> ToolResult:

        params = PlanParams(**invocation.params)

        try:
            action = params.action.lower()

            if action == 'add':
                if not params.content:
                    return ToolResult.error_result(
                        "Content is required for the 'add' action."
                    )

                step_id = str(uuid.uuid4())[:8]
                task = self.supervisor.state.get("task_checkpoint") or {}
                if task.get("status") != "in_progress":
                    self.supervisor.begin_task(params.content)

                self._steps[step_id] = params.content
                self._status[step_id] = PENDING
                self.supervisor.add_task_step(step_id, params.content)

                return ToolResult.success_result(
                    f"Added step [{step_id}]: {params.content}\n\n{self._listing()}",
                    metadata=self._snapshot(),
                )

            elif action == 'complete':
                if not params.id:
                    return ToolResult.error_result(
                        "An id is required for the 'complete' action."
                    )
                if params.id not in self._steps:
                    return ToolResult.error_result(
                        f"Plan step not found: {params.id}"
                    )

                if not params.verification:
                    return ToolResult.error_result(
                        "Verification evidence is required before a plan step can be completed."
                    )
                finished = self.supervisor.verify_task_step(
                    params.id, params.verification, passed=params.passed
                )
                self._status[params.id] = COMPLETED if params.passed else "failed"
                content = self._steps[params.id]

                message = f"{'Verified' if params.passed else 'Failed'} step [{params.id}]: {content}\n\n{self._listing()}"
                if not params.passed:
                    return ToolResult.error_result(
                        f"Verification failed for step [{params.id}].",
                        output=message,
                        metadata=self._snapshot(),
                        execution_status=ExecutionStatus.VERIFICATION_FAILED,
                        verification={"evidence": params.verification, "passed": False},
                    )
                return ToolResult.success_result(
                    message,
                    metadata=self._snapshot(),
                    execution_status=(
                        ExecutionStatus.TASK_COMPLETED if finished else ExecutionStatus.STATE_VERIFIED
                    ),
                    verification={
                        "evidence": params.verification,
                        "passed": True,
                        "expected_state_verified": True,
                        "user_objective_completed": finished,
                    },
                    expected_state_verified=True,
                    user_objective_completed=finished,
                )

            elif action == 'fail':
                if not params.id or params.id not in self._steps:
                    return ToolResult.error_result(f"Plan step not found: {params.id}")
                if not params.content:
                    return ToolResult.error_result("An error description is required to fail a step.")
                self.supervisor.fail_task_step(
                    params.id, params.content, params.recovery or "Inspect the latest state before retrying."
                )
                self._status[params.id] = "failed"
                return ToolResult.success_result(
                    f"Failed step [{params.id}]: {self._steps[params.id]}\n\n{self._listing()}",
                    metadata=self._snapshot(),
                )

            elif action == 'list':
                return ToolResult.success_result(
                    self._listing(),
                    metadata=self._snapshot(),
                )

            elif action == 'clear':
                count = len(self._steps)

                self.supervisor.cancel_task_checkpoint("Plan explicitly cleared by the agent.")
                self._steps.clear()
                self._status.clear()

                return ToolResult.success_result(
                    f"Cleared {count} plan step(s).",
                    metadata=self._snapshot(),
                )

            else:
                return ToolResult.error_result(
                    f"Unknown action: {params.action}"
                )

        except Exception as e:

            return ToolResult.error_result(
                f"Unknown error: {e}"
            )
