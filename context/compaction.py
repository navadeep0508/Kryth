"""Context compaction engine — manual /compact and automatic compaction.

Generates structured continuation summaries that preserve task state, protects
critical information from being summarised away, and performs atomic history
replacement so a failed compaction never corrupts conversation history.
"""

from __future__ import annotations

import asyncio
from typing import Any

from client.llm_client import LLMClient
from client.response import StreamEventType, TokenUsage
from config.config import Config
from context.manager import ContextManager, MessageItem
from prompts import get_compaction_prompt
from utils.text import count_tokens, estimate_request_tokens


# Sections that must never be summarised away.
_PROTECTED_ROLES = {"user", "system"}

# Minimum number of recent user messages to always preserve.
_MIN_RECENT_USER_MESSAGES = 2


class CompactionResult:
    """Outcome of a compaction run."""

    def __init__(
        self,
        *,
        succeeded: bool,
        previous_estimate: int,
        new_estimate: int,
        previous_utilization: float,
        new_utilization: float,
        messages_compacted: int,
        messages_preserved: int,
        task_checkpoint: dict[str, Any] | None,
        summary: str | None = None,
        error: str | None = None,
    ) -> None:
        self.succeeded = succeeded
        self.previous_estimate = previous_estimate
        self.new_estimate = new_estimate
        self.previous_utilization = previous_utilization
        self.new_utilization = new_utilization
        self.messages_compacted = messages_compacted
        self.messages_preserved = messages_preserved
        self.task_checkpoint = task_checkpoint
        self.summary = summary
        self.error = error


class ChatCompactor:
    """Compacts conversation history into a structured continuation summary."""

    def __init__(self, client: LLMClient, config: Config | None = None):
        self.client = client
        self.config = config
        self._compact_lock: asyncio.Lock | None = None
        self._last_compacted_range: tuple[int, int] | None = None

    async def _lock(self):
        # Simple asyncio lock lazily created when there's a running loop.
        import asyncio as _asyncio

        if self._compact_lock is None:
            self._compact_lock = _asyncio.Lock()
        return self._compact_lock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def compact(
        self,
        context_manager: ContextManager,
        *,
        preservation_instructions: str | None = None,
        task_snapshot: dict[str, Any] | None = None,
    ) -> CompactionResult:
        """Manually compact conversation history.

        Returns a :class:`CompactionResult`. On failure the original
        conversation is left untouched.
        """
        lock = await self._lock()
        async with lock:
            return await self._compact_locked(
                context_manager,
                preservation_instructions=preservation_instructions,
                task_snapshot=task_snapshot,
            )

    async def auto_compact(
        self,
        context_manager: ContextManager,
        *,
        request_estimate: int,
        context_window: int,
        task_snapshot: dict[str, Any] | None = None,
    ) -> CompactionResult | None:
        """Automatically compact when context pressure exceeds the threshold.

        Returns ``None`` when compaction is disabled or not needed.
        """
        if not self.config or not self.config.auto_compact_enabled:
            return None

        utilization = request_estimate / context_window if context_window else 0.0

        if utilization >= self.config.emergency_compact_threshold:
            lock = await self._lock()
            async with lock:
                return await self._compact_locked(
                    context_manager,
                    task_snapshot=task_snapshot,
                )

        if utilization >= self.config.auto_compact_threshold:
            lock = await self._lock()
            async with lock:
                return await self._compact_locked(
                    context_manager,
                    task_snapshot=task_snapshot,
                )

        return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _compact_locked(
        self,
        context_manager: ContextManager,
        *,
        preservation_instructions: str | None = None,
        task_snapshot: dict[str, Any] | None = None,
    ) -> CompactionResult:
        messages = context_manager.history
        if not messages:
            return CompactionResult(
                succeeded=True,
                previous_estimate=0,
                new_estimate=0,
                previous_utilization=0.0,
                new_utilization=0.0,
                messages_compacted=0,
                messages_preserved=0,
                task_checkpoint=task_snapshot,
            )

        # Estimate current request size before compaction.
        previous_estimate = self._estimate_request(
            context_manager.get_messages()
        )
        context_window = (
            self.config.effective_context_window if self.config else 256_000
        )
        previous_utilization = (
            previous_estimate / context_window if context_window else 0.0
        )

        # Idempotency: skip if all messages are protected (already compacted).
        # After a compaction, remaining messages are all protected, so
        # compactable will be empty and we return early below.

        # Split messages into compactable (older) and preserved (recent/system).
        # Protected: system messages, recent user messages, last 3 messages,
        # and the very last message.
        last_index = len(messages) - 1
        user_indices = [
            i for i, m in enumerate(messages) if m.role == "user"
        ]
        keep_user_count = min(_MIN_RECENT_USER_MESSAGES, len(user_indices))
        keep_user_indices = (
            set(user_indices[-keep_user_count:]) if user_indices else set()
        )

        protected_indices: set[int] = set()
        for i, m in enumerate(messages):
            if m.role == "system":
                protected_indices.add(i)
        protected_indices.add(last_index)
        for i in range(max(0, last_index - 2), last_index + 1):
            protected_indices.add(i)
        for i in keep_user_indices:
            protected_indices.add(i)

        compactable = [
            m for i, m in enumerate(messages) if i not in protected_indices
        ]
        preserved = [
            m for i, m in enumerate(messages) if i in protected_indices
        ]

        if not compactable:
            # Nothing can be compacted — all messages are protected.
            new_estimate = self._estimate_request(
                context_manager.get_messages()
            )
            return CompactionResult(
                succeeded=True,
                previous_estimate=previous_estimate,
                new_estimate=new_estimate,
                previous_utilization=previous_utilization,
                new_utilization=(
                    new_estimate / context_window if context_window else 0.0
                ),
                messages_compacted=0,
                messages_preserved=len(messages),
                task_checkpoint=task_snapshot,
                error="No eligible messages to compact.",
            )

        # Generate structured continuation summary.
        summary = await self._generate_summary(
            compactable,
            preserved,
            preservation_instructions=preservation_instructions,
            task_snapshot=task_snapshot,
        )

        if summary is None:
            return CompactionResult(
                succeeded=False,
                previous_estimate=previous_estimate,
                new_estimate=previous_estimate,
                previous_utilization=previous_utilization,
                new_utilization=previous_utilization,
                messages_compacted=0,
                messages_preserved=len(messages),
                task_checkpoint=task_snapshot,
                error="Summary generation failed.",
            )

        # Build the replacement user message.
        continuation = self._build_continuation_message(
            summary,
            task_snapshot=task_snapshot,
        )

        summary_item = MessageItem(
            role="user",
            content=continuation,
            token_count=count_tokens(continuation, self._model_name(context_manager)),
        )
        # Summary first (as context), then preserved messages in original order.
        new_messages = [summary_item] + list(preserved)
        old_messages = list(context_manager._messages)
        try:
            context_manager._messages = new_messages
            new_estimate = self._estimate_request(
                context_manager.get_messages()
            )
        finally:
            context_manager._messages = old_messages

        new_utilization = (
            new_estimate / context_window if context_window else 0.0
        )

        # Commit the replacement (atomic from the caller's perspective).
        context_manager._messages = new_messages
        context_manager._latest_usage = TokenUsage(
            prompt_tokens=new_estimate,
            total_tokens=new_estimate,
        )

        self._last_compacted_range = (0, len(messages) - 1)

        return CompactionResult(
            succeeded=True,
            previous_estimate=previous_estimate,
            new_estimate=new_estimate,
            previous_utilization=previous_utilization,
            new_utilization=new_utilization,
            messages_compacted=len(compactable),
            messages_preserved=len(preserved),
            task_checkpoint=task_snapshot,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _model_name(context_manager: ContextManager) -> str:
        return getattr(context_manager, "_model_name", "") or ""

    def _estimate_request(
        self,
        messages: list[dict[str, Any]],
    ) -> int:
        model = self.config.model_name if self.config else ""
        encoding = (
            self.config.effective_tokenizer_encoding if self.config else None
        )
        return estimate_request_tokens(
            messages,
            None,
            model=model,
            tokenizer_encoding=encoding,
        )

    def _find_split_index(self, messages: list[Any]) -> int:
        """Find where compactable messages end and preserved begin.

        Preserves:
        - All system messages
        - The last _MIN_RECENT_USER_MESSAGES user messages
        - The last 3 messages of any role (for conversational continuity)
        - The very last message regardless of role
        """
        if not messages:
            return 0

        last_index = len(messages) - 1

        # Collect recent user message indices to preserve.
        user_indices = [
            i for i, m in enumerate(messages) if m.role == "user"
        ]
        keep_user_count = min(_MIN_RECENT_USER_MESSAGES, len(user_indices))
        keep_user_indices = set(user_indices[-keep_user_count:]) if user_indices else set()

        # Walk from the end to find where compaction can start.
        # split_index = first index that CAN be compacted (everything before it
        # is compactable, everything from it onward is preserved).
        split_index = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]

            # The last message is always preserved.
            if i == last_index:
                continue

            # Keep last 3 messages of any role for continuity.
            if i > last_index - 3:
                continue

            # System messages always protected.
            if msg.role == "system":
                continue

            # Recent user messages protected.
            if msg.role == "user" and i in keep_user_indices:
                continue

            # Everything else can be compacted.
            split_index = i

        return split_index

    async def _generate_summary(
        self,
        compactable: list[Any],
        preserved: list[Any],
        *,
        preservation_instructions: str | None = None,
        task_snapshot: dict[str, Any] | None = None,
    ) -> str | None:
        """Generate a structured continuation summary via the LLM."""
        formatted = self._format_history_for_summary(compactable, preserved)

        preservation_section = ""
        if preservation_instructions:
            preservation_section = (
                f"\n## ADDITIONAL PRESERVATION INSTRUCTIONS\n{preservation_instructions}\n"
            )

        task_section = ""
        if task_snapshot:
            task_section = self._format_task_section(task_snapshot)

        prompt = f"""You are producing a structured continuation summary for an AI coding agent.
The conversation will be compacted and replaced with this summary. The agent
must be able to resume work seamlessly without repeating completed actions.

{task_section}{preservation_section}

## CONVERSATION TO COMPACT

{formatted}

## REQUIRED SUMMARY STRUCTURE

Produce a summary with EXACTLY these sections, in this order:

## Original User Objective
The actual objective of the ongoing task, in the user's own words where possible.

## Current Task
Task ID, current phase, and current status. If there is no active task, say so.

## Important Instructions
User requirements, restrictions, approval requirements, and any additional
preservation instructions provided above.

## Completed Work
Only steps that have been verified as completed. Include file paths, function
names, and specific changes made. Do NOT invent completed work.

## Pending Work
Steps that have been started but not completed, or are planned but not started.

## Failed Work
Failures, errors, and unresolved problems. If none, state "None."

## Technical Decisions
Important architectural and implementation decisions made during this session.

## Files and Code Changes
Relevant file paths and modifications. Be specific with paths.

## Tool State
Important tool outputs and references to persisted full output (e.g. file
paths where large tool outputs were saved).

## Last Verified State
What was actually observed and verified — not assumed.

## Next Action
The precise next step for continuing the task. Be specific and actionable.

Rules:
- Distinguish verified facts from assumptions. Label assumptions clearly.
- Do not invent completed work or verification evidence.
- Be specific with file paths, function names, and command names.
- Preserve all approval requirements and user restrictions.
- If the task checkpoint indicates an interrupted task, mark it as in-progress
  and do NOT claim completion.
"""

        messages = [
            {
                "role": "system",
                "content": get_compaction_prompt(),
            },
            {
                "role": "user",
                "content": prompt,
            },
        ]

        try:
            summary = ""

            async for event in self.client.chat_completion(
                messages,
                stream=False,
            ):
                if event.type == StreamEventType.MESSAGE_COMPLETE:
                    if event.text_delta:
                        summary += event.text_delta.content

            if not summary or not summary.strip():
                return None

            return summary.strip()
        except Exception:
            return None

    def _format_history_for_summary(
        self,
        compactable: list[Any],
        preserved: list[Any],
    ) -> str:
        """Format the compactable conversation history for the LLM."""
        parts: list[str] = []

        if compactable:
            parts.append("### Messages being compacted (older history):")
            for msg in compactable:
                role = msg.role
                content = msg.content or ""
                if role == "tool":
                    tool_id = msg.tool_call_id or "unknown"
                    truncated = content[:1500] if len(content) > 1500 else content
                    parts.append(f"\n[Tool Result ({tool_id})]:\n{truncated}")
                elif role == "assistant":
                    truncated = content[:2000] if len(content) > 2000 else content
                    parts.append(f"\n[Assistant]:\n{truncated}")
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            func = tc.get("function", {})
                            name = func.get("name", "unknown")
                            args = func.get("arguments", "{}")
                            if len(args) > 300:
                                args = args[:300] + "..."
                            parts.append(f"  Tool call: {name}({args})")
                else:
                    truncated = content[:1200] if len(content) > 1200 else content
                    parts.append(f"\n[User]:\n{truncated}")

        if preserved:
            parts.append("\n### Messages preserved (recent, kept verbatim):")
            for msg in preserved:
                role = msg.role
                content = msg.content or ""
                if role == "tool":
                    tool_id = msg.tool_call_id or "unknown"
                    truncated = content[:1500] if len(content) > 1500 else content
                    parts.append(f"\n[Tool Result ({tool_id})]:\n{truncated}")
                elif role == "assistant":
                    truncated = content[:2000] if len(content) > 2000 else content
                    parts.append(f"\n[Assistant]:\n{truncated}")
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            func = tc.get("function", {})
                            name = func.get("name", "unknown")
                            args = func.get("arguments", "{}")
                            if len(args) > 300:
                                args = args[:300] + "..."
                            parts.append(f"  Tool call: {name}({args})")
                else:
                    truncated = content[:1200] if len(content) > 1200 else content
                    parts.append(f"\n[User]:\n{truncated}")

        return "\n".join(parts)

    def _format_task_section(self, task_snapshot: dict[str, Any]) -> str:
        lines = ["## ACTIVE TASK CHECKPOINT"]
        if task_snapshot.get("task_id"):
            lines.append(f"- Task ID: {task_snapshot['task_id']}")
        if task_snapshot.get("description"):
            lines.append(f"- Description: {task_snapshot['description']}")
        if task_snapshot.get("status"):
            lines.append(f"- Status: {task_snapshot['status']}")
        if task_snapshot.get("current_phase"):
            lines.append(f"- Current phase: {task_snapshot['current_phase']}")
        if task_snapshot.get("completed_steps"):
            lines.append(
                f"- Completed steps: {'; '.join(task_snapshot['completed_steps'])}"
            )
        if task_snapshot.get("pending_steps"):
            lines.append(
                f"- Pending steps: {'; '.join(task_snapshot['pending_steps'])}"
            )
        if task_snapshot.get("failed_steps"):
            lines.append(
                f"- Failed steps: {'; '.join(task_snapshot['failed_steps'])}"
            )
        if task_snapshot.get("last_verified_state"):
            lines.append(f"- Last verified state: {task_snapshot['last_verified_state']}")
        if task_snapshot.get("recovery_information"):
            lines.append(f"- Recovery information: {task_snapshot['recovery_information']}")
        return "\n".join(lines) + "\n"

    def _build_continuation_message(
        self,
        summary: str,
        *,
        task_snapshot: dict[str, Any] | None = None,
    ) -> str:
        task_note = ""
        if task_snapshot:
            status = task_snapshot.get("status", "unknown")
            task_note = (
                f"\nThe active task ({task_snapshot.get('task_id', 'unknown')}) "
                f"has status '{status}'. "
            )
            if status == "in_progress":
                task_note += (
                    "Resume from the last verified step. Do NOT claim the task "
                    "has completed."
                )
            elif status == "completed":
                task_note += "The task has been completed."
            else:
                task_note += "Inspect current state before proceeding."

        return f"""# Context Restoration (Previous Session Compacted)

The previous conversation was compacted due to context length limits. Below is a
structured continuation summary of the work done so far.

**CRITICAL: Actions listed under "Completed Work" are already done. DO NOT
repeat them.**

---

{summary}

---

{task_note}

Resume work from where we left off. Focus ONLY on the remaining tasks."""
