from __future__ import annotations

from agent.agent import Agent
from agent.events import AgentEventType
from tools.base import InterruptionType
from ui.components import random_thinking_text, thinking_text_for
from ui.panels import PanelEventAdapter
from ui.tui import TUI


def _tool_kind(agent: Agent, tool_name: str) -> str | None:
    tool = agent.session.tool_registry.get(tool_name)
    return tool.kind.value if tool else None


async def stream_turn(tui: TUI, agent: Agent, message: str) -> str | None:

    final_response: str | None = None
    streaming = False
    failed = False
    label = random_thinking_text()

    tui.reset_turn_summary()
    agent.progress_callback = tui.tool_progress
    panel_events = PanelEventAdapter(
        start=tui.tool_call_start,
        complete=lambda data: tui.tool_call_complete(
            data.get("call_id", ""),
            data.get("name", "unknown"),
            _tool_kind(agent, data.get("name", "unknown")),
            data.get("success", False),
            data.get("output", ""),
            data.get("error"),
            data.get("metadata"),
            data.get("truncated", False),
            data.get("diff"),
            data.get("exit_code"),
        ),
        tool_kind=lambda name: _tool_kind(agent, name),
    )

    try:
        async for event in agent.run(message):
            if event.type == AgentEventType.AGENT_START:
                tui.start_thinking(label)

            elif event.type == AgentEventType.USAGE:
                tui.update_turn_usage(event.data.get("usage"), event.data.get("turn_usage"))

            elif event.type == AgentEventType.COMPACTION_DONE:
                tui.gap()
                tui.print_block(
                    f"[info]Context compacted: "
                    f"{event.data.get('previous_utilization', 0):.1%} → "
                    f"{event.data.get('new_utilization', 0):.1%} "
                    f"({event.data.get('messages_compacted', 0)} compacted, "
                    f"{event.data.get('messages_preserved', 0)} preserved)[/info]"
                )

            # Private reasoning events are intentionally not rendered. The
            # user sees concise activity labels and the assistant response.
            elif event.type in {
                AgentEventType.REASONING_DELTA,
                AgentEventType.REASONING_COMPLETE,
            }:
                continue

            elif event.type == AgentEventType.TEXT_DELTA:
                if not streaming:
                    tui.begin_assistant()
                    streaming = True
                tui.stream_assistant_delta(event.data.get("content", ""))

            elif event.type == AgentEventType.TEXT_COMPLETE:
                final_response = event.data.get("content")
                if streaming:
                    tui.end_assistant()
                    streaming = False

            elif event.type == AgentEventType.TOOL_CALL_START:
                panel_events.handle(event)

            elif event.type == AgentEventType.TOOL_CALL_COMPLETE:
                panel_events.handle(event)
                name = event.data.get("name", "unknown")
                kind = _tool_kind(agent, name)
                label = thinking_text_for(name, kind)
                tui.start_thinking(label)

            elif event.type == AgentEventType.AGENT_END:
                tui.stop_thinking()
                execution_status = event.data.get("execution_status")
                task_checkpoint = event.data.get("task_checkpoint") or {}
                interruption_type = event.data.get("interruption_type")
                interruption_reason = event.data.get("interruption_reason")
                interruption_exc_type = event.data.get("interruption_exception_type")
                interruption_exc_msg = event.data.get("interruption_exception_message")
                completed = (
                    execution_status == "TASK_COMPLETED"
                    or task_checkpoint.get("status") == "completed"
                )
                if completed:
                    tui.render_completion_summary()
                else:
                    if interruption_type:
                        tui.gap()
                        interruption_label = InterruptionType(interruption_type).value.replace("_", " ").title()
                        detail = interruption_reason or "Inspect current state before resuming."
                        if interruption_exc_type and interruption_exc_msg:
                            detail += f" ({interruption_exc_type}: {interruption_exc_msg})"
                        tui.print_block(
                            f"[warning]Task interrupted ({interruption_label}). {detail}[/warning]"
                        )
                    elif task_checkpoint.get("status") == "in_progress" or task_checkpoint.get("failed_steps"):
                        pending = task_checkpoint.get("pending_steps") or []
                        failed_steps = task_checkpoint.get("failed_steps") or []
                        details = []
                        if pending:
                            details.append("pending: " + ", ".join(pending))
                        if failed_steps:
                            details.append("failed: " + ", ".join(failed_steps))
                        tui.gap()
                        tui.print_block(
                            "[warning]Task remains incomplete; "
                            + ("; ".join(details) if details else "verification required")
                            + "[/warning]"
                        )
                    elif execution_status is None:
                        tui.gap()
                        tui.print_block("[warning]Task remains incomplete; verification required[/warning]")

            elif event.type == AgentEventType.AGENT_ERROR:
                tui.stop_thinking()
                tui.gap()
                tui.print_block(f"[error]{event.data.get('error')}[/error]")
                failed = True
                break
    finally:
        tui.stop_thinking()
        tui.end_reasoning()
        if streaming:
            tui.end_assistant()
        agent.progress_callback = None

    return None if failed else final_response
