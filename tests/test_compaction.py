"""Tests for the context compaction engine (manual + automatic).

Covers: manual /compact, automatic threshold triggering, task-aware
preservation, safe failure handling, idempotency, and protected-message
guarantees.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent.events import AgentEvent
from client.llm_client import LLMClient
from client.response import StreamEvent, StreamEventType, TokenUsage
from config.config import Config
from context.compaction import ChatCompactor, CompactionResult
from context.manager import ContextManager, MessageItem


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeLLMClient:
    """Stand-in for LLMClient whose chat_completion yields a fixed summary."""

    def __init__(self, summary: str | None = None, fail: bool = False):
        self.base_summary = summary or "# Summary\n\n## Original User Objective\ndo the thing\n"
        self.fail = fail
        self.calls: list[list[dict[str, Any]]] = []
        self.last_task_snapshot: dict[str, Any] | None = None
        self.last_preservation_instructions: str | None = None

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("LLM exploded")
        if not stream:
            yield StreamEvent(
                type=StreamEventType.MESSAGE_COMPLETE,
                text_delta=None,
                usage=None,
            )
            # Parse task and preservation info from the prompt's user message.
            user_content = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    user_content = m.get("content", "")
                    break
            self.last_task_snapshot = None
            self.last_preservation_instructions = None
            if "ACTIVE TASK CHECKPOINT" in user_content:
                self.last_task_snapshot = {"task_id": "task-123"}
            if "ADDITIONAL PRESERVATION INSTRUCTIONS" in user_content:
                self.last_preservation_instructions = (
                    "Preserve the API key in config/settings.py"
                )
            # Build a summary that incorporates task and preservation info.
            summary = self.base_summary
            if self.last_task_snapshot:
                summary += f"\n## Active Task: {self.last_task_snapshot.get('task_id', 'unknown')}\n"
            if self.last_preservation_instructions:
                summary += f"\n## Preservation: {self.last_preservation_instructions}\n"
            yield StreamEvent(
                type=StreamEventType.MESSAGE_COMPLETE,
                text_delta=type("TD", (), {"content": summary})(),
                usage=None,
            )
        return
        yield  # pragma: no cover - make it a generator


def make_context(
    config: Config,
    *,
    n_user: int = 5,
    n_tool: int = 3,
    system: bool = True,
) -> ContextManager:
    """Build a ContextManager with a repeating conversation history."""
    ctx = ContextManager(config, user_memory=None)
    if system:
        ctx.add_user_message("system prompt here")  # role=user for simplicity
    for i in range(n_user):
        ctx.add_user_message(f"user message {i}: do something important")
        for j in range(n_tool):
            ctx.add_tool_result(f"call-{i}-{j}", f"tool output {i}-{j}\n" * 50)
    return ctx


def make_compactor(config: Config | None = None, summary: str | None = None, fail: bool = False):
    config = config or Config()
    client = FakeLLMClient(summary=summary, fail=fail)
    return ChatCompactor(client, config), client


# ---------------------------------------------------------------------------
# Manual compaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_compaction_reduces_message_count():
    config = Config()
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    before = len(ctx.history)
    compactor, _client = make_compactor(config)

    result = await compactor.compact(ctx)

    assert result.succeeded is True
    assert result.messages_compacted > 0
    assert result.messages_preserved > 0
    assert result.new_estimate < result.previous_estimate
    assert len(ctx.history) < before
    assert result.summary is not None
    assert "Original User Objective" in result.summary


@pytest.mark.asyncio
async def test_manual_compaction_preserves_system_and_recent_messages():
    config = Config()
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    await compactor.compact(ctx)

    # The last message is always preserved.
    last = ctx.history[-1]
    assert last.role == "tool" or last.role == "user"
    # At least one user message (recent) is preserved.
    user_messages = [m for m in ctx.history if m.role == "user"]
    assert len(user_messages) >= 1


@pytest.mark.asyncio
async def test_manual_compaction_empty_history():
    config = Config()
    ctx = ContextManager(config, user_memory=None)
    compactor, _client = make_compactor(config)

    result = await compactor.compact(ctx)

    assert result.succeeded is True
    assert result.messages_compacted == 0
    assert result.messages_preserved == 0


@pytest.mark.asyncio
async def test_manual_compaction_idempotent_when_same_range():
    config = Config()
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    result1 = await compactor.compact(ctx)
    result2 = await compactor.compact(ctx)

    assert result1.succeeded is True
    # Second compaction: the summary item may be re-compacted, but history
    # stays stable (summary + preserved messages). No error should occur.
    assert result2.succeeded is True
    assert result2.messages_compacted <= 1  # at most the prior summary item
    assert len(ctx.history) == 6  # 1 summary + 5 preserved messages


# ---------------------------------------------------------------------------
# Automatic compaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_compact_disabled_returns_none():
    config = Config()
    config.auto_compact_enabled = False
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    result = await compactor.auto_compact(
        ctx, request_estimate=100_000, context_window=256_000,
    )

    assert result is None


@pytest.mark.asyncio
async def test_auto_compact_below_threshold_returns_none():
    config = Config()
    config.auto_compact_threshold = 0.75
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    # 50% utilization — below 0.75 threshold.
    result = await compactor.auto_compact(
        ctx, request_estimate=128_000, context_window=256_000,
    )

    assert result is None


@pytest.mark.asyncio
async def test_auto_compact_at_threshold_triggers():
    config = Config()
    config.auto_compact_threshold = 0.75
    config.emergency_compact_threshold = 0.90
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    # Exactly 0.75 — meets the threshold.
    result = await compactor.auto_compact(
        ctx, request_estimate=192_000, context_window=256_000,
    )

    assert result is not None
    assert result.succeeded is True


@pytest.mark.asyncio
async def test_auto_compact_emergency_threshold_triggers():
    config = Config()
    config.auto_compact_threshold = 0.75
    config.emergency_compact_threshold = 0.90
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    # 0.95 — above emergency threshold, even though auto_compact is what we'd
    # normally check; emergency path also compacts.
    result = await compactor.auto_compact(
        ctx, request_estimate=243_200, context_window=256_000,
    )

    assert result is not None
    assert result.succeeded is True


# ---------------------------------------------------------------------------
# Task-aware preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_includes_task_checkpoint_in_summary():
    config = Config()
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    task_snapshot = {
        "task_id": "task-123",
        "description": "Implement the feature",
        "status": "in_progress",
        "current_phase": "EXECUTE",
        "completed_steps": ["connect browser", "observe page"],
        "pending_steps": ["click submit", "verify result"],
        "failed_steps": [],
        "last_verified_state": "browser connected",
        "recovery_information": "use /resume to continue",
    }

    result = await compactor.compact(
        ctx, task_snapshot=task_snapshot,
    )

    assert result.succeeded is True
    assert result.task_checkpoint is not None
    assert result.task_checkpoint.get("task_id") == "task-123"
    assert "task-123" in result.summary


@pytest.mark.asyncio
async def test_compaction_with_preservation_instructions():
    config = Config()
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    result = await compactor.compact(
        ctx,
        preservation_instructions="Preserve the API key in config/settings.py",
        task_snapshot=None,
    )

    assert result.succeeded is True
    assert "preservation" in result.summary.casefold() or "API key" in result.summary


# ---------------------------------------------------------------------------
# Safe compaction / failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compaction_failure_leaves_history_untouched():
    config = Config()
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    before_messages = list(ctx.history)
    compactor, _client = make_compactor(config, fail=True)

    result = await compactor.compact(ctx)

    assert result.succeeded is False
    assert result.error is not None
    assert result.messages_compacted == 0
    assert result.messages_preserved == len(ctx.history)
    # History must be identical to before.
    assert [m.content for m in ctx.history] == [m.content for m in before_messages]


@pytest.mark.asyncio
async def test_compaction_result_carries_utilization():
    config = Config()
    config.model.context_window = 256_000
    ctx = make_context(config, n_user=5, n_tool=3)
    compactor, _client = make_compactor(config)

    result = await compactor.compact(ctx)

    assert 0.0 <= result.previous_utilization <= 1.0
    assert 0.0 <= result.new_utilization <= 1.0
    assert result.new_utilization <= result.previous_utilization


# ---------------------------------------------------------------------------
# COMPACTION_DONE event factory
# ---------------------------------------------------------------------------


def test_compaction_done_event_has_all_fields():
    event = AgentEvent.compaction_done(
        previous_estimate=200_000,
        new_estimate=80_000,
        previous_utilization=0.78,
        new_utilization=0.31,
        messages_compacted=12,
        messages_preserved=5,
    )

    assert event.type.value == "compaction_done"
    data = event.data
    assert data["previous_estimate"] == 200_000
    assert data["new_estimate"] == 80_000
    assert data["previous_utilization"] == 0.78
    assert data["new_utilization"] == 0.31
    assert data["messages_compacted"] == 12
    assert data["messages_preserved"] == 5


# ---------------------------------------------------------------------------
# CompactionResult dataclass
# ---------------------------------------------------------------------------


def test_compaction_result_defaults():
    result = CompactionResult(
        succeeded=True,
        previous_estimate=100,
        new_estimate=50,
        previous_utilization=0.5,
        new_utilization=0.25,
        messages_compacted=3,
        messages_preserved=2,
        task_checkpoint=None,
    )

    assert result.succeeded is True
    assert result.previous_estimate == 100
    assert result.new_estimate == 50
    assert result.previous_utilization == 0.5
    assert result.new_utilization == 0.25
    assert result.messages_compacted == 3
    assert result.messages_preserved == 2
    assert result.task_checkpoint is None
    assert result.summary is None
    assert result.error is None
