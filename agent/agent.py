from __future__ import annotations
import asyncio
import json
import re
import sys
import time
from typing import AsyncGenerator, Awaitable, Callable
from agent.session import Session
from agent.store import SessionRecord
from config.config import Config
from agent.events import AgentEvent, AgentEventType
from client.response import StreamEventType, TokenUsage, ToolCall, ToolResultMessage
from prompts import create_loop_breaker_prompt
from tools.base import (
    ContextBudgetExceeded,
    ExecutionStatus,
    InterruptionType,
    ToolConfirmation,
)
from utils.execution_record import ExecutionRecorder
from utils.text import estimate_request_tokens


def _confirms_new_project(message: str) -> bool:
    """Recognize a direct answer that authorizes use of a blank workspace."""
    normalized = re.sub(r"[^a-z0-9]+", " ", message.lower()).strip()
    return normalized in {
        "yes", "yes start here", "here", "new project", "new project here",
        "start a new project here", "start new project here", "start a new project",
        "start new project", "create a new project here", "create new project here",
    }


class Agent:
    def __init__(
        self,
        config: Config,
        confirmation_callback: Callable[[ToolConfirmation], Awaitable[bool]] | None = None,
        resume: str | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        self.config = config
        self.session: Session | None = Session(self.config)
        self.session.approval_manager.confirmation_callback = confirmation_callback
        self.execution_recorder = ExecutionRecorder(config.cwd)
        self._tool_started_at: dict[str, float] = {}

        self.progress_callback = progress_callback

        # Resolved on entry, once the session has a context manager to fill.
        self._resume = resume
        self.resumed: SessionRecord | None = None
        self.interruption_type: InterruptionType | None = None
        self.interruption_reason: str | None = None
        self.interruption_exception: BaseException | None = None

    async def run(self, message: str):
        await self.session.hook_system.trigger_before_agent(message)
        initial_task = self.session.development_supervisor.state.get("task_checkpoint") or {}
        initial_task_id = initial_task.get("task_id")
        resumed_incomplete_task = initial_task.get("status") == "in_progress"

        if self.session.project_context and self.session.project_context.is_ambiguous:
            if not _confirms_new_project(message):
                hint = (
                    self.session.project_context_hint
                    or f"Workspace `{self.session.config.cwd}` appears to be empty or new."
                )
                yield AgentEvent.agent_error(
                    "Clarification needed: "
                    + hint
                    + "\n\nDid you mean to start a new project here, or point KRYTH at a "
                      "different directory?",
                    details={
                        "ambiguous_workspace": True,
                        "workspace": str(self.session.config.cwd),
                    },
                )
                return

        checkpoint_steps = (initial_task.get("steps") or []) if resumed_incomplete_task else []
        execution_task_id = (
            initial_task.get("task_id") if resumed_incomplete_task
            else self.execution_recorder.find_incomplete(message)
        )
        self.execution_recorder.start(
            message,
            task_id=execution_task_id,
            plan=[step.get("description", "") for step in checkpoint_steps],
        )
        # Ensure a DevelopmentSupervisor task checkpoint exists for this turn.
        # This persists the task ID and step state so an interrupted task can
        # resume from its last verified step. Tests may inject a mock session
        # whose development_supervisor lacks begin_task; guard against that.
        if not resumed_incomplete_task:
            begin_task = getattr(self.session.development_supervisor, "begin_task", None)
            if begin_task:
                begin_task(message)
        self.execution_recorder.set_phase("UNDERSTAND")
        yield AgentEvent.agent_start(message)
        final_response: str | None = None
        finalized = False
        try:
            self.session.inc_turn()
            self.session.note_prompt(message)
            self.session.reset_turn_usage()
            self.session.context_manager.add_user_message(message)

            async for event in self._agentic_loop():
                if event.type == AgentEventType.CONTEXT_BUDGET:
                    self.execution_recorder.record_context_budget(event.data.get("context_budget") or {})
                elif event.type == AgentEventType.USAGE:
                    self.execution_recorder.record_usage(event.data.get("usage") or {})
                elif event.type == AgentEventType.AGENT_ERROR:
                    self.execution_recorder.set_phase("RECOVER")
                    self.execution_recorder.record_error(
                        str(event.data.get("error") or "Agent request failed"),
                        event.data.get("details"),
                    )
                elif event.type == AgentEventType.TOOL_CALL_START:
                    call_id = str(event.data.get("call_id", ""))
                    name = str(event.data.get("name", "unknown"))
                    arguments = event.data.get("arguments") or {}
                    self.execution_recorder.set_phase(self._phase_for_tool(name, arguments))
                    self.execution_recorder.tool_started(call_id, name, arguments)
                    self._tool_started_at[call_id] = time.perf_counter()
                elif event.type == AgentEventType.TEXT_COMPLETE:
                    final_response = event.data.get("content")
                yield event

            await self.session.hook_system.trigger_after_agent(message, final_response)
            self.session.save_checkpoint()
            current_task = self.session.development_supervisor.state.get("task_checkpoint") or {}
            task_relevant = resumed_incomplete_task or current_task.get("task_id") != initial_task_id
            task_checkpoint = current_task if task_relevant else None
            task_status = task_checkpoint.get("status") if task_checkpoint else None
            execution_status = (
                ExecutionStatus.TASK_COMPLETED if task_status == "completed" else None
            )
            self.execution_recorder.finalize(final_response, task_checkpoint=task_checkpoint)
            finalized = True
            yield AgentEvent.agent_end(
                final_response,
                self.session.last_usage,
                execution_status=execution_status,
                task_checkpoint=task_checkpoint,
                execution_record=str(self.execution_recorder.path) if self.execution_recorder.path else None,
                interruption_type=getattr(self, 'interruption_type', None),
                interruption_reason=getattr(self, 'interruption_reason', None),
                interruption_exception=getattr(self, 'interruption_exception', None),
            )
        finally:
            if not finalized:
                current_task = self.session.development_supervisor.state.get("task_checkpoint") or {}
                relevant = resumed_incomplete_task or current_task.get("task_id") != initial_task_id
                exc_type, exc_value, exc_tb = sys.exc_info()
                if exc_type is asyncio.CancelledError:
                    self.interruption_type = InterruptionType.USER_CANCELLED
                    self.interruption_reason = "User cancelled the turn (Ctrl+C or interrupt)."
                    self.interruption_exception = None
                elif isinstance(exc_value, ContextBudgetExceeded):
                    self.interruption_type = InterruptionType.CONTEXT_LIMIT_REACHED
                    cause = exc_value.cause or "local_exhaustion"
                    self.interruption_reason = (
                        f"Context budget exceeded: {exc_value} "
                        f"(estimated {exc_value.estimated_tokens} tokens / "
                        f"{exc_value.context_window} window; cause: {cause}). "
                        "Inspect current state before resuming."
                    )
                    self.interruption_exception = exc_value
                elif exc_type is TimeoutError:
                    self.interruption_type = InterruptionType.TOOL_TIMEOUT
                    self.interruption_reason = f"A tool call exceeded its timeout: {exc_value}"
                    self.interruption_exception = exc_value
                elif exc_type is ConnectionError or exc_type is OSError:
                    self.interruption_type = InterruptionType.CONNECTION_FAILED
                    self.interruption_reason = f"Connection failed: {exc_value}"
                    self.interruption_exception = exc_value
                elif exc_type is MemoryError:
                    # MemoryError is process RAM exhaustion, not model context overflow.
                    # Classify as generic execution interrupted so it is not confused
                    # with a context-window limit being reached.
                    self.interruption_type = InterruptionType.EXECUTION_INTERRUPTED
                    self.interruption_reason = f"Process memory exhausted: {exc_value}"
                    self.interruption_exception = exc_value
                elif exc_type is not None:
                    self.interruption_type = InterruptionType.EXECUTION_INTERRUPTED
                    self.interruption_reason = f"Execution interrupted: {exc_type.__name__}: {exc_value}"
                    self.interruption_exception = exc_value
                else:
                    self.interruption_type = InterruptionType.EXECUTION_INTERRUPTED
                    self.interruption_reason = "Execution was interrupted before completion."
                    self.interruption_exception = None
                self.execution_recorder.finalize(
                    final_response,
                    task_checkpoint=current_task if relevant else None,
                    interrupted=True,
                    interruption_type=self.interruption_type,
                    interruption_reason=self.interruption_reason,
                    interruption_exception=self.interruption_exception,
                )

    @staticmethod
    def _phase_for_tool(name: str, arguments: dict) -> str:
        if name == "plan":
            return "PLAN"
        if name in {"read", "list_directories", "grep", "glob", "search", "fetch", "memory"}:
            return "INSPECT"
        return "EXECUTE"
    
    async def _agentic_loop(self) -> AsyncGenerator[AgentEvent, None]:

        max_turns = self.config.max_turns

        for i in range(max_turns):
            if i:
                self.execution_recorder.set_phase("CONTINUE")
            self.execution_recorder.set_phase("PLAN")

            response_text = ""
            reasoning_text = ""
            reasoning_done = False

            # Prune before compacting: a tool-heavy turn can blow past the context
            # window without ever reaching the end of the loop.
            tool_schemas = self.session.tool_registry.get_schemas()
            self.session.context_manager.prune_tool_outputs()

            # Auto-compact before sending the model request when context pressure
            # exceeds the configured threshold. Estimate the serialized request
            # first so compaction can decide whether it's needed.
            current_messages = self.session.context_manager.get_messages()
            context_window = self.config.effective_context_window
            request_estimate = estimate_request_tokens(
                current_messages,
                None,
                model=self.config.model_name,
                tokenizer_encoding=self.config.effective_tokenizer_encoding,
            )
            task_snapshot = (
                self.session.development_supervisor.state.get("task_checkpoint") or {}
            )
            compaction_result = await self.session.chat_compactor.auto_compact(
                self.session.context_manager,
                request_estimate=request_estimate,
                context_window=context_window,
                task_snapshot=task_snapshot if task_snapshot.get("task_id") else None,
            )
            if compaction_result:
                yield AgentEvent.compaction_done(
                    previous_estimate=compaction_result.previous_estimate,
                    new_estimate=compaction_result.new_estimate,
                    previous_utilization=compaction_result.previous_utilization,
                    new_utilization=compaction_result.new_utilization,
                    messages_compacted=compaction_result.messages_compacted,
                    messages_preserved=compaction_result.messages_preserved,
                )

            tool_calls: list[ToolCall] = []
            usage: TokenUsage | None = None
            
            async for event in self.session.client.chat_completion(
                self.session.context_manager.get_messages(), 
                tools=tool_schemas if tool_schemas else None, 
                stream=True
            ):
                if event.type == StreamEventType.REASONING_DELTA:
                    if not event.reasoning_delta:
                        continue
                    reasoning_text += event.reasoning_delta
                    yield AgentEvent.reasoning_delta(event.reasoning_delta)
                elif event.type == StreamEventType.TEXT_DELTA:
                    if not event.text_delta:
                        continue
                    content = event.text_delta.content
                    # Thinking always ends where the answer begins.
                    if reasoning_text and not reasoning_done:
                        reasoning_done = True
                        yield AgentEvent.reasoning_complete(reasoning_text)
                    response_text += content
                    yield AgentEvent.text_delta(content)
                elif event.type == StreamEventType.TOOL_CALL_COMPLETE:
                    if event.tool_call:
                        tool_calls.append(event.tool_call)
                elif event.type == StreamEventType.MESSAGE_COMPLETE:
                    if event.reasoning_delta and not reasoning_text:
                        reasoning_text = event.reasoning_delta
                        yield AgentEvent.reasoning_delta(event.reasoning_delta)
                    if event.usage:
                        usage = event.usage
                        self.session.last_usage = event.usage
                        self.session.turn_usage += event.usage
                        yield AgentEvent.usage(event.usage, self.session.turn_usage)
                    if event.text_delta:
                        if reasoning_text and not reasoning_done:
                            reasoning_done = True
                            yield AgentEvent.reasoning_complete(reasoning_text)
                        response_text += event.text_delta.content
                        yield AgentEvent.text_delta(event.text_delta.content)
                    if event.tool_calls:
                        tool_calls.extend(event.tool_calls)
                elif event.type == StreamEventType.CONTEXT_BUDGET:
                    budget = event.context_budget or {}
                    self.session.context_budget_history.append(budget)
                    if len(self.session.context_budget_history) > 32:
                        del self.session.context_budget_history[:-32]
                    yield AgentEvent.context_budget(budget)
                elif event.type == StreamEventType.ERROR:
                    if reasoning_text and not reasoning_done:
                        reasoning_done = True
                        yield AgentEvent.reasoning_complete(reasoning_text)
                    if event.context_budget:
                        self.session.context_budget_history.append(event.context_budget)
                        if len(self.session.context_budget_history) > 32:
                            del self.session.context_budget_history[:-32]
                    yield AgentEvent.agent_error(
                        event.error or "Unknown error occurred.",
                        details={"context_budget": event.context_budget} if event.context_budget else None,
                    )
                    return

            # Reasoning with no answer after it: the model went straight to
            # tool calls, so nothing else will close the block.
            if reasoning_text and not reasoning_done:
                yield AgentEvent.reasoning_complete(reasoning_text)

            self.session.context_manager.add_assistant_message(
                response_text or None,
                [
                    {
                        'id': tc.call_id,
                        'type': 'function',
                        'function': {
                            'name': tc.name,
                            'arguments': json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ]
                if tool_calls
                else None
            )
        
            if response_text:
                yield AgentEvent.text_complete(response_text)
                self.session.loop_detector.record_action(
                    'response',
                    text=response_text
                )
            
            if not tool_calls:
                if usage:
                    self.session.context_manager.set_latest_usage(usage)
                    self.session.context_manager.add_usage(usage)

                return
            
            tool_call_results: list[ToolResultMessage] = []

            for tool_call in tool_calls:

                yield AgentEvent.tool_call_start(
                    tool_call.call_id,
                    tool_call.name,
                    tool_call.arguments
                )

                self.session.loop_detector.record_action(
                    "tool_call",
                    tool_name=tool_call.name,
                    args=tool_call.arguments
                )

                result = await self.session.tool_registry.invoke(
                    tool_call.name,
                    tool_call.arguments,
                    self.config.cwd,
                    self.session.hook_system,
                    self.session.approval_manager,
                    progress_callback=self.progress_callback,
                    call_id=tool_call.call_id,
                )

                started_at = self._tool_started_at.pop(tool_call.call_id, time.perf_counter())
                self.execution_recorder.tool_completed(
                    tool_call.call_id,
                    result,
                    duration_ms=(time.perf_counter() - started_at) * 1_000,
                )
                if tool_call.name == "plan" and isinstance(result.metadata.get("steps"), list):
                    self.execution_recorder.set_plan([
                        f"[{step.get('id')}] {step.get('content')} ({step.get('status')})"
                        for step in result.metadata["steps"]
                        if isinstance(step, dict)
                    ])
                if not result.success:
                    self.execution_recorder.set_phase("RECOVER")
                elif result.execution_status in {ExecutionStatus.STATE_VERIFIED, ExecutionStatus.TASK_COMPLETED}:
                    self.execution_recorder.set_phase("VERIFY")
                else:
                    self.execution_recorder.set_phase("OBSERVE")

                yield AgentEvent.tool_call_complete(
                    tool_call.call_id,
                    tool_call.name,
                    result,
                )

                tool_call_results.append(
                    ToolResultMessage(
                        tool_call_id=tool_call.call_id,
                        content=result.to_model_output(),
                        is_error=not result.success
                    )
                )

            for tool_result in tool_call_results:
                self.session.context_manager.add_tool_result(
                    tool_result.tool_call_id,
                    tool_result.content,
                )
                history = self.session.context_manager.history
                if history:
                    content = history[-1].content
                    marker = "Full output saved at: "
                    if marker in content:
                        reference = content.split(marker, 1)[1].splitlines()[0].strip()
                        self.execution_recorder.set_tool_output_reference(
                            tool_result.tool_call_id, reference
                        )

            loop_detection_err = self.session.loop_detector.check_for_loop()
                            
            if loop_detection_err:
            
                loop_prompt = create_loop_breaker_prompt(loop_detection_err)
            
                self.session.context_manager.add_user_message(loop_prompt)
                continue
    
    async def __aenter__(self) -> Agent:
        await self.session.initalize(progress=self.progress_callback)
        if self._resume:
            self.resumed = self.session.resume(self._resume)
        return self
    
    async def __aexit__(
            self, 
            exc_type, 
            exc_val, 
            exc_tb
        ) -> None:

        if self.session:
            try:
                if self.session.client:
                    await self.session.client.close()
            finally:
                try:
                    if self.session.mcp_manager:
                        await self.session.mcp_manager.shutdown()
                finally:
                    self.session = None # Close session, everything else will be garbage collected
