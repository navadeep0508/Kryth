from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace

from prompt_toolkit.keys import Keys
from rich.console import Console

from tools.base import FileDiff, ToolConfirmation
from ui.components.confirmation import confirmation_request
from ui.components.terminal_approval import (
    ApprovalAction,
    RiskLevel,
    TerminalApprovalPanel,
    classify_risk,
)
from ui.theme import AGENT_THEME


def render(panel: TerminalApprovalPanel, width: int = 90, *, no_color: bool = False) -> str:
    stream = StringIO()
    console = Console(
        file=stream, record=True, width=width, theme=AGENT_THEME, no_color=no_color
    )
    console.print(panel)
    return console.export_text()


def test_risk_classification_covers_readonly_destructive_and_unknown_commands():
    assert classify_risk("dir").value == "low"
    assert classify_risk("python scripts/run.py").value == "medium"
    assert classify_risk("Get-ChildItem -Recurse | Remove-Item -Recurse -Force").value == "high"
    assert classify_risk("diskpart").value == "critical"
    assert classify_risk("unknown-tool --custom").value == "medium"


def test_approval_interface_is_compact_defaults_to_allow_once_and_shows_scope(tmp_path):
    command = "Get-ChildItem -Recurse | Remove-Item -Recurse -Force"
    confirmation = ToolConfirmation(
        tool_name="bash",
        params={"command": command},
        description=f"Execute: {command}",
        command=command,
        risk_level="high",
        shell_type="powershell",
    )
    panel = TerminalApprovalPanel(confirmation, tmp_path)
    rendered = render(panel)
    panel.move(1)
    session_rendered = render(panel)

    assert "APPROVAL REQUIRED" in rendered
    assert "PowerShell | HIGH" in rendered
    assert "Remove files or directories" in rendered
    assert "> [Y] Allow once" in rendered
    assert "[A] Allow for session" in session_rendered
    assert command not in rendered
    assert "FULL COMMAND" not in rendered
    assert "Ctrl+O" in rendered
    panel.move(-1)
    assert panel.selected_action == ApprovalAction.ALLOW_ONCE
    assert "+---" not in rendered


def test_expanded_approval_preserves_multiline_command_exactly_once(tmp_path):
    command = "Get-ChildItem -Recurse `\n  | Remove-Item -Recurse -Force '缓存'"
    confirmation = ToolConfirmation(
        tool_name="bash", params={"command": command, "cwd": str(tmp_path)},
        description=f"Execute: {command}", command=command, shell_type="powershell",
    )
    panel = TerminalApprovalPanel(confirmation, tmp_path)
    panel.toggle_details()
    rendered = render(panel, width=55)

    assert "DETAILS" in rendered
    assert "Approval scope:" in rendered
    assert "FULL COMMAND" in rendered
    assert "Get-ChildItem -Recurse" in rendered
    assert "Remove-Item -Recurse -Force" in rendered
    assert "缓存" in rendered
    assert rendered.count("Remove-Item") == 1
    assert all(len(line) <= 55 for line in rendered.splitlines())


def test_file_approval_uses_same_interface_and_describes_file_operation(tmp_path):
    path = tmp_path / "配置.py"
    confirmation = ToolConfirmation(
        tool_name="write", params={"path": str(path), "content": "x = 1"},
        description=f"Create file: {path}",
        diff=FileDiff(path, "", "x = 1\n", is_new_file=True),
    )
    prompt = confirmation_request(confirmation, tmp_path)

    assert len(prompt) == 1
    rendered = render(prompt[0])
    prompt[0].move(1)
    session_rendered = render(prompt[0])
    assert "APPROVAL REQUIRED" in rendered
    assert "Write" in rendered
    assert "Create" in rendered
    assert "Allow once" in rendered
    assert "> [A] Allow for session" in session_rendered


def test_selector_navigation_expansion_and_rejection_actions():
    confirmation = ToolConfirmation(
        tool_name="bash", params={"command": "echo ok"}, description="Execute: echo ok", command="echo ok"
    )
    panel = TerminalApprovalPanel(confirmation)
    panel.move(1)
    assert panel.selected_action == ApprovalAction.ALLOW_SESSION
    panel.move(1)
    assert panel.selected_action == ApprovalAction.REJECT
    panel.move(10)
    assert panel.selected == 2
    panel.toggle_details()
    assert panel.expanded


def test_tui_confirmation_keys_navigate_expand_and_confirm():
    from config.config import Config
    from ui.tui import TUI

    async def run():
        tui = TUI(Config(), Console(record=True, theme=AGENT_THEME))
        loop = asyncio.get_running_loop()
        pending = loop.create_future()
        tui._pending_confirmation = pending
        tui._approval_panel = TerminalApprovalPanel(ToolConfirmation(
            tool_name="bash", params={"command": "echo ok"}, description="Execute: echo ok", command="echo ok"
        ))
        tui.feed_confirmation_key(SimpleNamespace(key=Keys.Down, data=""))
        tui.feed_confirmation_key(SimpleNamespace(key=Keys.ControlO, data=""))
        tui.feed_confirmation_key(SimpleNamespace(key=Keys.ControlM, data="\r"))
        assert pending.result() == ApprovalAction.ALLOW_SESSION
        assert tui._approval_panel.expanded

    asyncio.run(run())


def test_direct_approval_shortcuts_and_escape_are_one_shot():
    from config.config import Config
    from ui.tui import TUI

    async def check(data, key, expected):
        tui = TUI(Config(), Console(record=True, theme=AGENT_THEME))
        pending = asyncio.get_running_loop().create_future()
        tui._pending_confirmation = pending
        tui._approval_panel = TerminalApprovalPanel(ToolConfirmation(
            tool_name="bash", params={"command": "echo ok"},
            description="Execute: echo ok", command="echo ok",
        ))
        consumed = tui.feed_confirmation_key(SimpleNamespace(key=key, data=data))
        assert consumed and pending.result() == expected
        # Further key events cannot change the already recorded decision.
        tui.feed_confirmation_key(SimpleNamespace(key=Keys.Down, data=""))
        assert pending.result() == expected

    async def run():
        await check("y", None, ApprovalAction.ALLOW_ONCE)
        await check("a", None, ApprovalAction.ALLOW_SESSION)
        await check("n", None, ApprovalAction.REJECT)
        await check("", Keys.Escape, ApprovalAction.CANCELLED)

    asyncio.run(run())


def test_ctrl_c_during_approval_cancels_and_is_consumed():
    """Ctrl+C during a pending approval sets CANCELLED and is consumed (returns True)."""
    from config.config import Config
    from ui.tui import TUI

    async def run():
        tui = TUI(Config(), Console(record=True, theme=AGENT_THEME))
        pending = asyncio.get_running_loop().create_future()
        tui._pending_confirmation = pending
        tui._approval_panel = TerminalApprovalPanel(ToolConfirmation(
            tool_name="bash", params={"command": "echo ok"}, description="Execute: echo ok", command="echo ok"
        ))
        consumed = tui.feed_confirmation_key(SimpleNamespace(key=Keys.ControlC, data=""))
        assert consumed
        assert pending.done()
        assert pending.result() == ApprovalAction.CANCELLED

    asyncio.run(run())


def test_rejection_cannot_be_reported_as_approval():
    from config.config import Config
    from ui.tui import TUI

    async def run():
        console = Console(record=True, theme=AGENT_THEME)
        tui = TUI(Config(), console)

        async def reject(_panel):
            return ApprovalAction.REJECT

        tui._read_approval_interactive = reject
        confirmation = ToolConfirmation(
            tool_name="bash", params={"command": "unknown-tool"},
            description="Execute: unknown-tool", command="unknown-tool",
        )
        assert not await tui.confirm_tool(confirmation)
        assert "REJECTED  Execution blocked" in console.export_text()

    asyncio.run(run())


def test_ascii_safe_no_color_and_narrow_terminal_rendering():
    confirmation = ToolConfirmation(
        tool_name="bash", params={"command": "python -m pytest -q"},
        description="Execute: python -m pytest -q", command="python -m pytest -q", shell_type="cmd",
    )
    rendered = render(TerminalApprovalPanel(confirmation), width=42, no_color=True)
    assert "APPROVAL REQUIRED" in rendered
    assert "> [Y] Allow once" in rendered
    assert all(len(line) <= 42 for line in rendered.splitlines())
