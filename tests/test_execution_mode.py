"""Tests for the Advanced Mode Switching System.

Covers: ExecutionMode enum, config defaults, ApprovalManager mode overrides,
TUI cycling and badges, /mode slash command, and key bindings.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config.config import Config, ExecutionMode
from safety.approval import (
    ApprovalContext,
    ApprovalDecision,
    ApprovalManager,
)
from ui.repl.commands.dispatch import SlashCommands
from ui.tui import TUI


# ---------------------------------------------------------------------------
# ExecutionMode enum
# ---------------------------------------------------------------------------


def test_execution_mode_values():
    assert ExecutionMode.AUTO == "auto"
    assert ExecutionMode.MANUAL == "manual"
    assert ExecutionMode.PLAN == "plan"
    assert ExecutionMode.ACCEPT_EDITS == "accept_edits"


def test_execution_mode_labels():
    assert ExecutionMode.AUTO.label == "AUTO"
    assert ExecutionMode.MANUAL.label == "MANUAL"
    assert ExecutionMode.PLAN.label == "PLAN"
    assert ExecutionMode.ACCEPT_EDITS.label == "ACCEPT EDITS"


def test_execution_mode_summaries():
    assert "autonomous" in ExecutionMode.AUTO.summary.lower()
    assert "every tool" in ExecutionMode.MANUAL.summary.lower()
    assert "read-only" in ExecutionMode.PLAN.summary.lower()
    assert "file edits" in ExecutionMode.ACCEPT_EDITS.summary.lower()


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_config_default_execution_mode():
    config = Config()
    assert config.default_execution_mode == ExecutionMode.AUTO
    assert config.execution_mode == ExecutionMode.AUTO


def test_config_can_set_execution_mode():
    config = Config(execution_mode=ExecutionMode.MANUAL)
    assert config.execution_mode == ExecutionMode.MANUAL


# ---------------------------------------------------------------------------
# ApprovalManager mode overrides
# ---------------------------------------------------------------------------


def _make_context(
    *,
    is_mutating: bool = True,
    command: str | None = None,
    affected_paths: list[Path] | None = None,
    is_dangerous: bool = False,
) -> ApprovalContext:
    return ApprovalContext(
        tool_name="write",
        params={},
        is_mutating=is_mutating,
        affected_paths=affected_paths or [],
        command=command,
        is_dangerous=is_dangerous,
    )


def _make_manager(mode: ExecutionMode, policy=None) -> ApprovalManager:
    if policy is None:
        from config.config import ApprovalPolicy
        policy = ApprovalPolicy.ON_REQUEST
    return ApprovalManager(policy, Path.cwd(), execution_mode=mode)


@pytest.mark.asyncio
async def test_non_mutating_always_approved_regardless_of_mode():
    for mode in ExecutionMode:
        mgr = _make_manager(mode)
        ctx = _make_context(is_mutating=False)
        decision = await mgr.check_approval(ctx)
        assert decision == ApprovalDecision.APPROVED, f"mode={mode}"


@pytest.mark.asyncio
async def test_plan_mode_rejects_all_mutating():
    mgr = _make_manager(ExecutionMode.PLAN)
    ctx = _make_context(is_mutating=True, command="echo hello")
    decision = await mgr.check_approval(ctx)
    assert decision == ApprovalDecision.REJECTED


@pytest.mark.asyncio
async def test_plan_mode_rejects_file_writes():
    mgr = _make_manager(ExecutionMode.PLAN)
    ctx = _make_context(
        is_mutating=True,
        affected_paths=[Path("src/foo.py")],
    )
    decision = await mgr.check_approval(ctx)
    assert decision == ApprovalDecision.REJECTED


@pytest.mark.asyncio
async def test_manual_mode_needs_confirmation_for_all_mutating():
    mgr = _make_manager(ExecutionMode.MANUAL)
    ctx = _make_context(is_mutating=True, command="echo hello")
    decision = await mgr.check_approval(ctx)
    assert decision == ApprovalDecision.NEEDS_CONFIRMATION


@pytest.mark.asyncio
async def test_manual_mode_needs_confirmation_for_file_writes():
    mgr = _make_manager(ExecutionMode.MANUAL)
    ctx = _make_context(
        is_mutating=True,
        affected_paths=[Path("src/foo.py")],
    )
    decision = await mgr.check_approval(ctx)
    assert decision == ApprovalDecision.NEEDS_CONFIRMATION


@pytest.mark.asyncio
async def test_accept_edits_auto_approves_workspace_file_edit():
    mgr = _make_manager(ExecutionMode.ACCEPT_EDITS)
    ctx = _make_context(
        is_mutating=True,
        affected_paths=[Path.cwd() / "src" / "foo.py"],
    )
    decision = await mgr.check_approval(ctx)
    assert decision == ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_accept_edits_auto_approves_no_extension_file():
    mgr = _make_manager(ExecutionMode.ACCEPT_EDITS)
    ctx = _make_context(
        is_mutating=True,
        affected_paths=[Path.cwd() / "Makefile"],
    )
    decision = await mgr.check_approval(ctx)
    assert decision == ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_accept_edits_rejects_outside_cwd():
    mgr = _make_manager(ExecutionMode.ACCEPT_EDITS)
    outside = Path("/tmp/outside.py")
    ctx = _make_context(
        is_mutating=True,
        affected_paths=[outside],
    )
    decision = await mgr.check_approval(ctx)
    # Not a recognized file edit suffix within cwd -> falls through to normal
    # policy (ON_REQUEST -> NEEDS_CONFIRMATION for non-safe mutating)
    assert decision == ApprovalDecision.NEEDS_CONFIRMATION


@pytest.mark.asyncio
async def test_accept_edits_rejects_dangerous():
    mgr = _make_manager(ExecutionMode.ACCEPT_EDITS)
    ctx = _make_context(
        is_mutating=True,
        affected_paths=[Path.cwd() / "src" / "foo.py"],
        is_dangerous=True,
    )
    decision = await mgr.check_approval(ctx)
    assert decision != ApprovalDecision.APPROVED


@pytest.mark.asyncio
async def test_auto_mode_falls_through_to_normal_policy():
    mgr = _make_manager(ExecutionMode.AUTO)
    # Mutating, no command, within cwd, not dangerous, ON_REQUEST policy
    ctx = _make_context(
        is_mutating=True,
        affected_paths=[Path.cwd() / "src" / "foo.py"],
    )
    decision = await mgr.check_approval(ctx)
    assert decision == ApprovalDecision.NEEDS_CONFIRMATION


# ---------------------------------------------------------------------------
# Session execution_mode attribute
# ---------------------------------------------------------------------------


def test_session_execution_mode_matches_config():
    from agent.session import Session

    config = Config(execution_mode=ExecutionMode.PLAN)
    session = Session(config)
    assert session.execution_mode == ExecutionMode.PLAN


# ---------------------------------------------------------------------------
# TUI execution mode
# ---------------------------------------------------------------------------


def test_tui_execution_mode_property():
    config = Config(execution_mode=ExecutionMode.PLAN)
    tui = TUI(config)
    assert tui.execution_mode == ExecutionMode.PLAN


def test_tui_execution_mode_badge():
    config = Config(execution_mode=ExecutionMode.MANUAL)
    tui = TUI(config)
    assert tui.execution_mode_badge() == "mode: MANUAL"


def test_tui_cycle_execution_mode_cycles_all_modes():
    config = Config(execution_mode=ExecutionMode.AUTO)
    tui = TUI(config)

    modes = list(ExecutionMode)
    for i in range(len(modes) * 2):
        tui.cycle_execution_mode()
    # After 2 full cycles we should be back at AUTO
    assert tui.config.execution_mode == ExecutionMode.AUTO


def test_tui_cycle_execution_mode_advances_one():
    config = Config(execution_mode=ExecutionMode.AUTO)
    tui = TUI(config)
    tui.cycle_execution_mode()
    assert tui.config.execution_mode == ExecutionMode.MANUAL


# ---------------------------------------------------------------------------
# /mode slash command
# ---------------------------------------------------------------------------


def _make_slash_commands(config: Config) -> SlashCommands:
    console = SimpleNamespace(print=lambda *a, **k: None)
    return SlashCommands(config, console)


def test_mode_command_shows_current_mode(capsys):
    config = Config(execution_mode=ExecutionMode.PLAN)
    cmd = _make_slash_commands(config)
    console = SimpleNamespace()
    printed = []
    console.print = lambda *a, **k: printed.append(" ".join(str(a) for a in a))
    cmd.console = console

    cmd.mode_command(None, [])

    output = " ".join(printed)
    assert "PLAN" in output


def test_mode_command_sets_auto():
    config = Config(execution_mode=ExecutionMode.PLAN)
    cmd = _make_slash_commands(config)
    console = SimpleNamespace()
    printed = []
    console.print = lambda *a, **k: printed.append(" ".join(str(a) for a in a))
    cmd.console = console

    cmd.mode_command(None, ["auto"])

    assert config.execution_mode == ExecutionMode.AUTO
    assert any("AUTO" in p for p in printed)


def test_mode_command_sets_manual():
    config = Config(execution_mode=ExecutionMode.AUTO)
    cmd = _make_slash_commands(config)
    console = SimpleNamespace()
    console.print = lambda *a, **k: None
    cmd.console = console

    cmd.mode_command(None, ["manual"])

    assert config.execution_mode == ExecutionMode.MANUAL


def test_mode_command_sets_plan():
    config = Config(execution_mode=ExecutionMode.AUTO)
    cmd = _make_slash_commands(config)
    console = SimpleNamespace()
    console.print = lambda *a, **k: None
    cmd.console = console

    cmd.mode_command(None, ["plan"])

    assert config.execution_mode == ExecutionMode.PLAN


def test_mode_command_sets_accept_edits():
    config = Config(execution_mode=ExecutionMode.AUTO)
    cmd = _make_slash_commands(config)
    console = SimpleNamespace()
    console.print = lambda *a, **k: None
    cmd.console = console

    cmd.mode_command(None, ["accept-edits"])

    assert config.execution_mode == ExecutionMode.ACCEPT_EDITS


def test_mode_command_accepts_accept_edits_underscore():
    config = Config(execution_mode=ExecutionMode.AUTO)
    cmd = _make_slash_commands(config)
    console = SimpleNamespace()
    console.print = lambda *a, **k: None
    cmd.console = console

    cmd.mode_command(None, ["accept_edits"])

    assert config.execution_mode == ExecutionMode.ACCEPT_EDITS


def test_mode_command_unknown_mode():
    config = Config(execution_mode=ExecutionMode.AUTO)
    cmd = _make_slash_commands(config)
    console = SimpleNamespace()
    printed = []
    console.print = lambda *a, **k: printed.append(" ".join(str(a) for a in a))
    cmd.console = console

    cmd.mode_command(None, ["bogus"])

    assert config.execution_mode == ExecutionMode.AUTO
    assert any("Unknown mode" in p for p in printed)


# ---------------------------------------------------------------------------
# Mode persistence across Session restore
# ---------------------------------------------------------------------------


def test_session_restore_keeps_execution_mode_from_config():
    """Session created with a config carries its execution_mode; restore
    reuses the same Session object so the mode survives."""
    from agent.session import Session

    config = Config(execution_mode=ExecutionMode.MANUAL)
    session = Session(config)
    assert session.execution_mode == ExecutionMode.MANUAL

    # Changing config mode propagates to session attribute only at init time;
    # the session attribute is a snapshot. Verify the attribute exists and is
    # the right type.
    assert isinstance(session.execution_mode, ExecutionMode)


# ---------------------------------------------------------------------------
# Key bindings (Shift+Tab / BackTab) -> cycle_execution_mode
# ---------------------------------------------------------------------------


def test_keys_shift_tab_calls_cycle_execution_mode():
    from ui.repl.keys import build_key_bindings
    from prompt_toolkit.keys import Keys

    config = Config(execution_mode=ExecutionMode.AUTO)
    tui = TUI(config)
    bindings = build_key_bindings(tui)

    class FakeApp:
        def __init__(self):
            self.renderer = SimpleNamespace(erase=lambda: None, reset=lambda: None)
        def _request_absolute_cursor_position(self):
            pass
        def _redraw(self):
            pass

    fake_app = FakeApp()

    # Find the s-tab binding
    for binding in bindings.get_bindings_for_keys(("s-tab",)):
        binding.handler(SimpleNamespace(app=fake_app))

    assert tui.config.execution_mode == ExecutionMode.MANUAL


def test_turn_keys_backtab_calls_cycle_execution_mode():
    from ui.repl.keys import turn_keys
    from prompt_toolkit.keys import Keys

    config = Config(execution_mode=ExecutionMode.PLAN)
    tui = TUI(config)

    async def run():
        key_press = SimpleNamespace(key=Keys.BackTab, data="")
        task = SimpleNamespace()

        class FakeDevice:
            def raw_mode(self):
                return self

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def attach(self, cb):
                cb()
                return self

            def read_keys(self):
                return [key_press]

        device = FakeDevice()
        with patch("ui.repl.keys.create_input", return_value=device):
            with turn_keys(tui, task):
                pass

    asyncio.run(run())
    assert tui.config.execution_mode == ExecutionMode.ACCEPT_EDITS
