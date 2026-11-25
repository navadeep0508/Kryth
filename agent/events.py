from __future__ import annotations
from enum import Enum
from typing import Any
from dataclasses import dataclass, field
from client.response import TokenUsage
from tools.base import ExecutionStatus, InterruptionType, ToolResult

class AgentEventType(str, Enum):

    # Agentic loops, and err
    AGENT_START="agent_start"
    AGENT_END="agent_end"
    AGENT_ERROR="agent_error"

    # Token accounting, emitted once per completion
    USAGE = "usage"
    CONTEXT_BUDGET = "context_budget"
    COMPACTION_DONE = "compaction_done"

    # Text streaming
    TEXT_DELTA = "text_delta"
    TEXT_COMPLETE = "text_complete"

    # Reasoning ("thinking") streaming, only for models that expose it
    REASONING_DELTA = "reasoning_delta"
    REASONING_COMPLETE = "reasoning_complete"

    # Tool Calls
    TOOL_CALL_START = 'tool_call_start'
    TOOL_CALL_COMPLETE = 'tool_call_complete'

@dataclass
class AgentEvent:
    type: AgentEventType
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def agent_start(
        cls, 
        message: str
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.AGENT_START,
            data={
                "message": message
            }
        )

    @classmethod
    def agent_end(
        cls,
        response: str | None = None,
        usage: TokenUsage | None = None,
        execution_status: ExecutionStatus | None = None,
        task_checkpoint: dict[str, Any] | None = None,
        execution_record: str | None = None,
        interruption_type: InterruptionType | str | None = None,
        interruption_reason: str | None = None,
        interruption_exception: BaseException | None = None,
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.AGENT_END,
            data={
                "response": response,
                "usage": usage.__dict__ if usage else None,
                "execution_status": execution_status.value if execution_status else None,
                "task_checkpoint": task_checkpoint,
                "execution_record": execution_record,
                "interruption_type": (interruption_type.value if hasattr(interruption_type, "value") else str(interruption_type)) if interruption_type else None,
                "interruption_reason": interruption_reason,
                "interruption_exception_type": type(interruption_exception).__name__ if interruption_exception else None,
                "interruption_exception_message": str(interruption_exception) if interruption_exception else None,
            },
        )
    
    @classmethod
    def usage(cls, request_usage: TokenUsage, turn_usage: TokenUsage | None = None) -> AgentEvent:
        cumulative = turn_usage or request_usage
        return cls(
            type=AgentEventType.USAGE,
            data={
                'usage': request_usage.__dict__,
                'turn_usage': cumulative.__dict__,
            }
        )

    @classmethod
    def context_budget(cls, budget: dict[str, Any]) -> AgentEvent:
        return cls(type=AgentEventType.CONTEXT_BUDGET, data={"context_budget": budget})

    @classmethod
    def compaction_done(
        cls,
        *,
        previous_estimate: int,
        new_estimate: int,
        previous_utilization: float,
        new_utilization: float,
        messages_compacted: int,
        messages_preserved: int,
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.COMPACTION_DONE,
            data={
                "previous_estimate": previous_estimate,
                "new_estimate": new_estimate,
                "previous_utilization": previous_utilization,
                "new_utilization": new_utilization,
                "messages_compacted": messages_compacted,
                "messages_preserved": messages_preserved,
            },
        )

    @classmethod
    def agent_error(
        cls, 
        error: str, 
        details: dict[str, Any] | None = None
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.AGENT_ERROR,
            data={
                "error": error,
                'details': details or {} 
            }
        )

    @classmethod
    def text_delta(
        cls, 
        content: str
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.TEXT_DELTA,
            data={
                "content": content
            }
        )

    @classmethod
    def text_complete(
        cls, 
        content: str
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.TEXT_COMPLETE,
            data={
                "content": content
            }
        )
    
    @classmethod
    def reasoning_delta(
        cls,
        content: str
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.REASONING_DELTA,
            data={
                "content": content
            }
        )

    @classmethod
    def reasoning_complete(
        cls,
        content: str
    ) -> AgentEvent:
        return cls(
            type=AgentEventType.REASONING_COMPLETE,
            data={
                "content": content
            }
        )

    @classmethod
    def tool_call_start(cls, call_id: str, name: str, arguments: dict[str, Any]):
        return cls(
            type=AgentEventType.TOOL_CALL_START,
            data={
                'call_id': call_id,
                'name': name,
                'arguments': arguments
            }
        )
    
    @classmethod
    def tool_call_complete(cls, call_id: str, name: str, result: ToolResult):
        return cls(
            type=AgentEventType.TOOL_CALL_COMPLETE,
            data={
                'call_id': call_id,
                'name': name,
                'success': result.success,
                'output': result.output,
                'error': result.error,
                'metadata': result.metadata,
                'diff': result.diff.create_diff() if result.diff else None,
                'truncated': result.truncated,
                'exit_code': result.exit_code,
                'execution_status': result.execution_status.value if result.execution_status else None,
                'action_executed': result.action_executed,
                'state_changed': result.state_changed,
                'expected_state_verified': result.expected_state_verified,
                'user_objective_completed': result.user_objective_completed,
                'verification': result.verification,
            }
        )
