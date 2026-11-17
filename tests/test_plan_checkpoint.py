from __future__ import annotations

import asyncio

from config.config import Config
from tools.base import ExecutionStatus, ToolInvocation
from tools.core.plan import PlanTool


def test_plan_is_restored_across_turns_and_completes_only_after_all_steps_verified(tmp_path):
    async def run():
        tool = PlanTool(Config(cwd=tmp_path))
        first = await tool.execute(ToolInvocation(
            params={"action": "add", "content": "Inspect browser state"}, cwd=tmp_path
        ))
        step1 = first.metadata["steps"][0]["id"]
        second = await tool.execute(ToolInvocation(
            params={"action": "add", "content": "Verify search result"}, cwd=tmp_path
        ))
        step2 = next(step["id"] for step in second.metadata["steps"] if step["id"] != step1)
        tool.reset()
        assert len(tool._snapshot()["steps"]) == 2

        no_evidence = await tool.execute(ToolInvocation(
            params={"action": "complete", "id": step1}, cwd=tmp_path
        ))
        assert not no_evidence.success
        assert "Verification evidence is required" in no_evidence.error

        verified = await tool.execute(ToolInvocation(
            params={"action": "complete", "id": step1,
                    "verification": "Search page URL and result title observed"}, cwd=tmp_path
        ))
        assert verified.execution_status is ExecutionStatus.STATE_VERIFIED
        assert verified.metadata["task_status"] == "in_progress"

        finished = await tool.execute(ToolInvocation(
            params={"action": "complete", "id": step2,
                    "verification": "Selected page URL and title observed"}, cwd=tmp_path
        ))
        assert finished.execution_status is ExecutionStatus.TASK_COMPLETED
        assert finished.metadata["task_status"] == "completed"

    asyncio.run(run())


def test_plan_verification_failure_never_marks_task_complete(tmp_path):
    async def run():
        tool = PlanTool(Config(cwd=tmp_path))
        added = await tool.execute(ToolInvocation(
            params={"action": "add", "content": "Open target page"}, cwd=tmp_path
        ))
        step_id = added.metadata["steps"][0]["id"]
        result = await tool.execute(ToolInvocation(
            params={"action": "complete", "id": step_id, "passed": False,
                    "verification": "Final URL stayed on the source page"}, cwd=tmp_path
        ))
        assert not result.success
        assert result.execution_status is ExecutionStatus.VERIFICATION_FAILED
        assert result.metadata["task_status"] == "in_progress"
        assert result.metadata["failed"] == 1

    asyncio.run(run())
