"""UI v4 — presentation-layer contract.

Verifies the premium redesign without touching backend behavior:
  • signature palette = exact v4 hexes (#DFFF00 + 6-stop gradient + dark bgs)
  • premium header, status bar, planner panel, tool cards, debug panel all
    render without error and emit no protocol leakage
  • debug panel is hidden in normal mode, shown in debug mode
  • event-driven only — these are pure renderers driven by caller data
"""

import io
import re
import sys
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.ui import theme


# ── Palette ──────────────────────────────────────────────────────────────────

def test_signature_color():
    assert theme.ACCENT_PRIMARY == "#DFFF00"


def test_v4_gradient_stops():
    assert theme.GRADIENT == (
        "#DFFF00", "#D7FF10", "#CAFB22", "#BAF03B", "#A6E256", "#91D473",
    )


def test_v4_dark_backgrounds():
    assert theme.BG_BASE == "#050505"
    assert theme.BG_PANEL == "#090909"
    assert theme.BG_RAISED == "#101010"
    assert theme.BG_ELEV == "#161616"


def test_v4_text_and_state_colors():
    assert theme.TEXT_PRIMARY == "#FFFFFF"
    assert theme.TEXT_SECONDARY == "#D8D8D8"
    assert theme.TEXT_MUTED == "#8A8A8A"
    assert theme.STATE_SUCCESS == "#6EFF7B"
    assert theme.STATE_WARNING == "#FFB84D"
    assert theme.STATE_ERROR == "#FF5A5A"


def test_theme_object_builds():
    # Rich Theme must construct with every style key resolvable.
    from rich.console import Console
    Console(theme=theme.THEME)


def test_gradient_rule_renders():
    t = theme.gradient_rule(30)
    assert t is not None and len(str(t)) >= 30


# ── Render helpers (capture stdout, strip ANSI) ──────────────────────────────

def _capture(fn):
    """Capture real Rich output by temporarily routing the shared console to a
    recording file, then reading rendered (ANSI-stripped) text."""
    from agent.ui.console import console
    buf = io.StringIO()
    orig_file = console.file
    try:
        console.file = buf
        fn()
    finally:
        console.file = orig_file
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


def test_header_renders():
    from agent.ui.components import banner
    out = _capture(lambda: banner("qwen/qwen3-coder", "https://integrate.api.nvidia.com/v1", 24))
    assert "KRYTH" in out
    assert "Provider" in out
    # No protocol leakage in the header.
    for bad in ("<think>", "<tool_call>", "<|channel|>", "<display>"):
        assert bad not in out


def test_status_bar_renders():
    from agent.ui.components import status_bar
    out = _capture(lambda: status_bar(provider="NVIDIA", tools=3, tokens=2418,
                                      adapter="Native", telemetry_on=True))
    assert "Provider" in out and "NVIDIA" in out
    assert "Tools" in out and "Telemetry" in out


def test_planner_panel_renders_without_logic():
    from agent.ui.components import planner_panel
    out = _capture(lambda: planner_panel(
        goal="Fix parser bug", current="Editing llm.py",
        completed=["Analyze stream", "Locate issue"], next_step="Run regression tests"))
    assert "Planner" in out
    assert "Fix parser bug" in out
    assert "Editing llm.py" in out
    assert "Analyze stream" in out
    assert "Run regression tests" in out


def test_planner_panel_empty_is_noop():
    from agent.ui.components import planner_panel
    out = _capture(lambda: planner_panel())
    assert out.strip() == ""


def test_tool_card_renders():
    from agent.ui.components import tool_card
    out = _capture(lambda: tool_card("Read File", "src/agent/llm.py", state="start"))
    assert "READ FILE" in out
    assert "src/agent/llm.py" in out


def test_tool_card_states():
    from agent.ui.components import tool_card
    for st in ("start", "done", "error"):
        out = _capture(lambda st=st: tool_card("Write File", "a.py", state=st))
        assert "WRITE FILE" in out


def test_debug_panel_hidden_in_normal_mode(monkeypatch):
    from agent.ui.components import debug_panel
    monkeypatch.delenv("KRYTH_DEBUG_UI", raising=False)
    out = _capture(lambda: debug_panel(model="m", base_url="u", tools=3))
    assert out.strip() == ""


def test_debug_panel_shown_in_debug_mode(monkeypatch):
    from agent.ui.components import debug_panel
    monkeypatch.setenv("KRYTH_DEBUG_UI", "1")
    out = _capture(lambda: debug_panel(model="qwen/q", base_url="https://integrate.api.nvidia.com/v1", tools=24))
    assert "Runtime Debug" in out
    assert "Provider" in out
    assert "Adapter" in out
    assert "TTFT" in out
    assert "Harmony sanitized" in out


def test_spinner_frames_available():
    assert theme.BRAILLE_FRAMES[0] == "⠋"
    assert "◐" in theme.CIRCLE_FRAMES
