import asyncio
import json
from types import SimpleNamespace

from agent.agent import Agent
from agent.events import AgentEvent, AgentEventType
from client.response import TokenUsage
from config.config import Config
from tools.base import ExecutionStatus, ToolResult
from utils.execution_record import ExecutionRecorder


class _Hooks:
    async def trigger_before_agent(self, _message):
        return None

    async def trigger_after_agent(self, _message, _response):
        return None


class _Context:
    def add_user_message(self, _message):
        return None


class _Session:
    def __init__(self, config):
        self.config = config
        self.hook_system = _Hooks()
        self.development_supervisor = SimpleNamespace(state={"task_checkpoint": {"status": "idle"}})
        self.project_context = None
        self.context_manager = _Context()
        self.last_usage = None

    def inc_turn(self):
        return None

    def note_prompt(self, _message):
        return None

    def reset_turn_usage(self):
        return None

    def save_checkpoint(self):
        return None


def test_agent_records_context_and_never_promotes_plain_response_to_verified_completion(tmp_path):
    async def events():
        yield AgentEvent.context_budget({
            "status": "admitted", "estimated_input_tokens": 120, "input_budget": 1_000,
        })
        yield AgentEvent.usage(TokenUsage(prompt_tokens=120, completion_tokens=12, total_tokens=132))
        yield AgentEvent(type=AgentEventType.TEXT_COMPLETE, data={"content": "Updated the file."})

    async def run():
        config = Config(cwd=tmp_path)
        agent = Agent.__new__(Agent)
        agent.config = config
        agent.session = _Session(config)
        agent.execution_recorder = ExecutionRecorder(tmp_path)
        agent._tool_started_at = {}
        agent._agentic_loop = events
        return [event async for event in agent.run("Update the file")]

    result = asyncio.run(run())
    record_files = list((tmp_path / ".kryth" / "executions").glob("*.json"))
    stored = json.loads(record_files[0].read_text(encoding="utf-8"))

    assert result[-1].type is AgentEventType.AGENT_END
    assert result[-1].data["execution_status"] is None
    assert result[-1].data["execution_record"] == str(record_files[0])
    assert stored["status"] == "answered_unverified"
    assert stored["user_objective_completed"] is False
    assert stored["context_budget_stats"]["requests"] == 1
    assert stored["token_usage"]["total_tokens"] == 132


def test_tool_result_status_fields_keep_action_and_outcome_separate():
    result = ToolResult.success_result(
        "Clicked a link",
        execution_status=ExecutionStatus.ACTION_EXECUTED,
        action_executed=True,
        state_changed=False,
        expected_state_verified=False,
        user_objective_completed=False,
    )

    assert result.action_executed is True
    assert result.state_changed is False
    assert result.expected_state_verified is False
    assert result.user_objective_completed is False
