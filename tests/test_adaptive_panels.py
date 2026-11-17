from __future__ import annotations

import asyncio
from io import StringIO

from rich.console import Console

from agent.events import AgentEvent
from config.config import Config
from tools.base import ToolInvocation, ToolResult
from tools.core.read import ReadFileTool
from tools.core.write import WriteFileTool
from ui.components.tool_call import ToolOutcome
from ui.panels import PanelEventAdapter, PanelOrchestrator, PanelRendererRegistry, PanelStatus
from ui.panels.render import render_panel
from ui.theme import AGENT_THEME
from ui.tui import TUI


def test_panel_registry_covers_every_default_registered_tool():
    from tools.registry import create_default_registry

    registered = {tool.name for tool in create_default_registry(Config()).get_tools()}
    renderers = PanelRendererRegistry()
    assert registered <= renderers.registered_names


def test_panel_registry_renders_success_failure_and_dynamic_mcp_fallbacks():
    from tools.registry import create_default_registry

    names = {tool.name for tool in create_default_registry(Config()).get_tools()}
    registry = PanelRendererRegistry()
    for name in names:
        success, details = registry.render(ToolOutcome(
            name=name, args={"path": "example.py", "goal": "inspect"},
            output="result", metadata={"path": "example.py", "total_lines": 1},
        ))
        failure, _failure_details = registry.render(ToolOutcome(
            name=name, success=False, error="expected test failure",
        ))
        assert success or details
        assert failure

    summary, detail = registry.render(ToolOutcome(
        name="mcp_server__custom_tool", args={"query": "status"}, output="ok"
    ))
    assert "MCP" in summary[0].plain
    assert detail


def test_panel_lifecycle_deduplicates_calls_and_terminal_events():
    panels = PanelOrchestrator()
    state, created = panels.start("call-1", "read", "read", {"path": "a.py"})
    duplicate, created_again = panels.start("call-1", "read", "read", {"path": "a.py"})
    assert created and not created_again and state is duplicate

    panels.update_progress("reading file")
    completed, changed = panels.complete(
        "call-1", success=True, output="1|value", error=None,
        metadata={"path": "a.py", "total_lines": 1}, truncated=False,
        diff=None, exit_code=None,
    )
    _same, changed_again = panels.complete(
        "call-1", success=True, output="duplicate", error=None,
        metadata={}, truncated=False, diff=None, exit_code=None,
    )
    assert changed and not changed_again
    assert completed is state and state.status == PanelStatus.COMPLETED
    assert "1 lines" in str(state.summary[0])


def test_panel_lifecycle_represents_approval_and_cancellation():
    panels = PanelOrchestrator()
    state, _ = panels.start("call-approval", "bash", "bash", {"command": "echo ok"})
    assert panels.wait_for_approval() is state
    assert state.status == PanelStatus.WAITING_APPROVAL
    assert panels.resume_after_approval() is state
    assert state.status == PanelStatus.RUNNING
    assert panels.cancel(state.call_id) is state
    assert state.status == PanelStatus.CANCELLED


def test_panel_event_adapter_maps_existing_agent_lifecycle_events():
    panels = PanelOrchestrator()
    received: list[dict] = []
    adapter = PanelEventAdapter(
        start=lambda *args: panels.start(*args),
        complete=received.append,
        tool_kind=lambda _name: "read",
    )
    assert adapter.handle(AgentEvent.tool_call_start("c1", "read", {"path": "x"}))
    complete = AgentEvent.tool_call_complete("c1", "read", ToolResult.success_result("ok"))
    assert adapter.handle(complete)
    assert received[0]["call_id"] == "c1"
    assert not adapter.handle(AgentEvent.agent_start("hello"))


def test_noninteractive_tui_prints_one_final_panel_for_a_call():
    output = StringIO()
    console = Console(
        file=output, record=True, force_terminal=False, width=80, theme=AGENT_THEME
    )
    tui = TUI(Config(), console)
    tui.tool_call_start("c2", "read", "read", {"path": "sample.py"})
    tui.tool_progress("reading")
    tui.tool_call_complete(
        "c2", "read", "read", True, "1|value", None,
        {"path": "sample.py", "total_lines": 1}, False, None, None,
    )
    tui.tool_call_complete(
        "c2", "read", "read", True, "duplicate", None, {}, False, None, None,
    )
    text = console.export_text()
    assert text.count("read     sample.py") == 1
    assert "done" in text.lower()
    assert "+---" not in text and "| read" not in text


def test_ten_sequential_tool_calls_render_as_ten_borderless_activity_rows():
    output = StringIO()
    console = Console(
        file=output, record=True, force_terminal=False, width=90, theme=AGENT_THEME
    )
    tui = TUI(Config(), console)
    for index in range(10):
        call_id = f"call-{index}"
        target = f"src/module_{index}.py"
        tui.tool_call_start(call_id, "read", "read", {"path": target})
        tui.tool_call_complete(
            call_id, "read", "read", True, "1|value", None,
            {"path": target, "total_lines": 1}, False, None, None,
        )
    rendered = console.export_text()
    assert rendered.count("done") == 10
    assert all(f"module_{index}.py" in rendered for index in range(10))
    assert "+---" not in rendered and "| read" not in rendered


def test_plan_snapshot_updates_in_place_and_resets_for_new_turn():
    output = StringIO()
    console = Console(file=output, record=True, theme=AGENT_THEME, width=90)
    tui = TUI(Config(), console)
    tui.tool_call_start("plan-1", "plan", "memory", {"action": "add"})
    tui.tool_call_complete(
        "plan-1", "plan", "memory", True, "plan", None,
        {"completed": 0, "total": 2, "steps": [
            {"id": "1", "content": "Inspect project", "status": "in_progress"},
            {"id": "2", "content": "Validate imports", "status": "pending"},
        ]}, False, None, None,
    )
    assert tui.plan_summary == "PLAN 0/2"
    tui.tool_call_start("plan-2", "plan", "memory", {"action": "complete"})
    tui.tool_call_complete(
        "plan-2", "plan", "memory", True, "plan", None,
        {"completed": 1, "total": 2, "steps": [
            {"id": "1", "content": "Inspect project", "status": "completed"},
            {"id": "2", "content": "Validate imports", "status": "pending"},
        ]}, False, None, None,
    )
    assert tui.plan_summary == "PLAN 1/2"
    assert "[done]" in tui.plan_view().renderables[1].plain
    assert console.export_text() == ""
    prompt_view = "".join(fragment[1] for fragment in tui.expansion_fragments())
    assert prompt_view.count("PLAN  1/2 completed") == 1
    assert "[done] Inspect project" in prompt_view
    assert "[next] Validate imports" in prompt_view
    tui.reset_plan_view()
    assert tui.plan_summary is None


def test_running_and_failed_activity_rows_are_compact_and_width_bounded():
    panels = PanelOrchestrator()
    running, _ = panels.start("run-1", "bash", "bash", {"command": "pytest -q"})
    running.progress = "Running tests"
    running_row = render_panel(running, width=52).plain
    assert "run      pytest -q" in running_row
    assert "Running tests" in running_row
    assert "running" in running_row
    assert len(running_row) <= 52

    failed, _ = panels.start("read-2", "read", "read", {"path": "src/a.py"})
    failed, _ = panels.complete(
        "read-2", success=False, output="", error="Permission denied: src/a.py",
        metadata={}, truncated=False, diff=None, exit_code=None,
    )
    failed_row = render_panel(failed, width=52).plain
    assert "read     src/a.py" in failed_row
    assert "failed" in failed_row
    assert "Permission deni..." in failed_row
    assert "+---" not in failed_row


def test_activity_expansion_keeps_output_and_arguments_accessible():
    panels = PanelOrchestrator()
    state, _ = panels.start("bash-1", "bash", "bash", {"command": "pytest -q"})
    state, _ = panels.complete(
        "bash-1", success=True, output="4 passed, 0 failed", error=None,
        metadata={}, truncated=False, diff=None, exit_code=0,
    )
    compact = render_panel(state, width=72).plain
    expanded = render_panel(state, expanded=True, width=72)
    console = Console(record=True, theme=AGENT_THEME, width=72)
    console.print(expanded)
    expanded_text = console.export_text()
    assert "pytest -q" in compact
    assert "4 passed" not in compact
    assert "4 passed, 0 failed" in expanded_text
    assert "pytest -q" in expanded_text


def test_ctrl_o_expansion_during_execution_is_kept_after_completion():
    output = StringIO()
    console = Console(file=output, record=True, theme=AGENT_THEME, width=80)
    tui = TUI(Config(), console)
    tui.tool_call_start("run-expand", "bash", "bash", {"command": "pytest -q"})
    tui.toggle_expansion()
    tui.tool_call_complete(
        "run-expand", "bash", "bash", True, "4 passed", None,
        {}, False, None, 0,
    )

    assert "4 passed" in console.export_text()


def test_completion_summary_only_reports_observed_metrics():
    output = StringIO()
    console = Console(file=output, record=True, theme=AGENT_THEME, width=80)
    tui = TUI(Config(), console)
    tui.reset_turn_summary()
    tui.tool_call_start("read-1", "read", "read", {"path": "README.md"})
    tui.tool_call_complete(
        "read-1", "read", "read", True, "1|hello", None,
        {"path": "README.md", "total_lines": 1}, False, None, None,
    )
    tui.render_completion_summary()
    rendered = console.export_text()
    assert "COMPLETED" in rendered
    assert "Files inspected      1" in rendered
    assert "Files modified" not in rendered
    assert "Tests passed" not in rendered


def test_read_and_write_tools_execute_and_render_real_results(tmp_path):
    config = Config()
    writer = WriteFileTool(config)
    reader = ReadFileTool(config)

    async def execute():
        written = await writer.execute(ToolInvocation(
            params={"path": "nested/example.txt", "content": "panel test\n"},
            cwd=tmp_path,
        ))
        read = await reader.execute(ToolInvocation(
            params={"path": "nested/example.txt"}, cwd=tmp_path,
        ))
        return written, read

    written, read = asyncio.run(execute())
    assert written.success and read.success
    assert "panel test" in read.output
    panels = PanelOrchestrator()
    state, _ = panels.start("write", "write", "write", {"path": "nested/example.txt"})
    state, changed = panels.complete(
        "write", success=written.success, output=written.output, error=written.error,
        metadata=written.metadata, truncated=written.truncated,
        diff=written.diff.create_diff() if written.diff else None, exit_code=None,
    )
    assert changed and state is not None
    assert "Created" in state.summary[0].plain or "Updated" in state.summary[0].plain
