from rich.console import Console

from ui.components.tool_call import (
    RENDERERS,
    ToolOutcome,
    tool_blocks,
    tool_header,
    tool_status,
)
from ui.theme import AGENT_THEME


def plain(renderables) -> str:
    console = Console(record=True, theme=AGENT_THEME, width=80, color_system=None)
    for renderable in renderables:
        console.print(renderable)
    return console.export_text()


def test_every_persistent_terminal_tool_has_a_specific_renderer():
    names = {
        "terminal_start",
        "terminal_status",
        "terminal_logs",
        "terminal_input",
        "terminal_stop",
        "terminal_restart",
        "terminal_list",
    }
    assert names <= RENDERERS.keys()


def test_every_builtin_tool_has_a_specific_renderer():
    from tools.core import get_all_core_tools

    names = {tool.name for tool in get_all_core_tools()}
    assert names <= RENDERERS.keys()


def test_directory_tool_name_maps_to_directory_renderer():
    outcome = ToolOutcome(
        name="list_directories",
        output="src/\nREADME.md",
        metadata={"path": "src", "entries": 2},
    )

    summary, details = tool_blocks(outcome)
    assert "2 entries" in plain(summary)
    assert details


def test_terminal_logs_show_session_and_line_count_before_details():
    outcome = ToolOutcome(
        name="terminal_logs",
        output="server ready\nGET / 200",
        args={"name": "web"},
        metadata={"session": "web", "status": "running", "returned_lines": 2},
    )

    summary, details = tool_blocks(outcome)
    assert "web" in plain(summary)
    assert "Running" in plain(summary)
    assert "2 lines" in plain(summary)
    assert "server ready" in plain(details)


def test_tool_failure_surfaces_error_category_and_exit_code():
    outcome = ToolOutcome(
        name="bash",
        success=False,
        error="Command failed",
        metadata={"error_category": "SHELL_MISMATCH"},
        exit_code=2,
    )

    summary, _details = tool_blocks(outcome)
    rendered = plain(summary)
    assert "Command failed" in rendered
    assert "shell mismatch" in rendered
    assert "exit 2" in rendered


def test_long_tool_failure_is_short_by_default_and_available_when_expanded():
    message = "A" * 240 + "\nDetailed cause: permission denied"
    summary, details = tool_blocks(ToolOutcome(name="bash", success=False, error=message))

    assert len(plain(summary).strip()) <= 165
    assert "Detailed cause: permission denied" in plain(details)


def test_tool_header_uses_readable_name_and_concise_result():
    header = tool_header(">", "tool", "terminal_start", "web", tool_status(True, "1.2s"))

    assert "Terminal start" in header.plain
    assert "web" in header.plain
    assert "done" in header.plain
    assert "1.2s" in header.plain


def test_subagent_call_summarizes_role_termination_and_tool_use():
    outcome = ToolOutcome(
        name="subagent_code_reviewer",
        output=(
            "Sub-agent 'code_reviewer' completed.\n"
            "Termination: goal\n"
            "Tools called: read, grep\n\n"
            "Result:\nNo issues found."
        ),
    )

    summary, details = tool_blocks(outcome)
    rendered = plain(summary)
    assert "code reviewer" in rendered
    assert "goal" in rendered
    assert "2 tools" in rendered
    assert "No issues found" in plain(details)


def test_subagent_tools_have_a_specialized_summary():
    from tools.subagents.subagents import get_default_subagent_definitions

    for definition in get_default_subagent_definitions():
        name = f"subagent_{definition.name}"
        summary, _details = tool_blocks(
            ToolOutcome(name=name, output="Termination: goal\nTools called: read")
        )
        assert definition.name.replace("_", " ") in plain(summary)
