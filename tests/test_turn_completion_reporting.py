import asyncio
from types import SimpleNamespace

from agent.events import AgentEvent
from tools.base import ExecutionStatus
from ui.stream import stream_turn


class _TUI:
    def __init__(self):
        self.completion_summaries = 0
        self.messages = []

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None

    def render_completion_summary(self):
        self.completion_summaries += 1

    def print_block(self, *args):
        self.messages.extend(args)


class _Agent:
    def __init__(self, events):
        self.events = events
        self.session = SimpleNamespace(tool_registry=SimpleNamespace(get=lambda _name: None))
        self.progress_callback = None

    async def run(self, _message):
        for event in self.events:
            yield event


def test_files_inspected_or_unverified_answer_does_not_print_completed():
    tui = _TUI()
    agent = _Agent([
        AgentEvent.agent_start("Inspect files"),
        AgentEvent.agent_end(
            "Inspected three files.",
            execution_status=None,
            task_checkpoint=None,
        ),
    ])

    asyncio.run(stream_turn(tui, agent, "Inspect files"))

    assert tui.completion_summaries == 0
    assert any("incomplete" in str(message).casefold() for message in tui.messages)


def test_only_verified_task_checkpoint_prints_completed():
    tui = _TUI()
    checkpoint = {"status": "completed", "pending_steps": [], "failed_steps": []}
    agent = _Agent([
        AgentEvent.agent_start("Finish the task"),
        AgentEvent.agent_end(
            "Done.",
            execution_status=ExecutionStatus.TASK_COMPLETED,
            task_checkpoint=checkpoint,
        ),
    ])

    asyncio.run(stream_turn(tui, agent, "Finish the task"))

    assert tui.completion_summaries == 1
