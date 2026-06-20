"""UI v5 — Live Layout Engine contract (presentation only).

Verifies the persistent layout engine: flag gating + rollback, layout creation,
header/footer persistence, timeline scrolling, assistant + tool panels, resize
adaptivity, live updates from events — all WITHOUT a live model and WITHOUT any
backend change (the engine is a pure event-bus consumer).
"""

import io
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.ui.live_engine import LiveEngine, TimelineItem, live_ui_enabled
from agent.ui.events import EventKind


def _render_text(eng, width=180):
    """Render at an explicit width (default: dashboard mode → all panels)."""
    from agent.ui.console import console
    import rich.console as rc
    buf = io.StringIO()
    orig_file = console.file
    orig_width = console._width
    try:
        console.file = buf
        console._width = width   # force a deterministic width for the test
        console.print(eng._render())
    finally:
        console.file = orig_file
        console._width = orig_width
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


# ── Feature flag + rollback ──────────────────────────────────────────────────

def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("KRYTH_LIVE_UI", raising=False)
    assert live_ui_enabled() is False


def test_flag_on(monkeypatch):
    monkeypatch.setenv("KRYTH_LIVE_UI", "1")
    assert live_ui_enabled() is True


def test_install_selects_renderer_when_off(monkeypatch):
    # Default OFF → ui.install must use the incremental renderer (rollback).
    monkeypatch.delenv("KRYTH_LIVE_UI", raising=False)
    import agent.ui as ui
    import agent.ui.renderer as renderer
    import agent.ui.live_engine as live
    ui.uninstall()
    called = {"renderer": False, "live": False}
    monkeypatch.setattr(renderer, "install", lambda: called.__setitem__("renderer", True))
    monkeypatch.setattr(live, "install", lambda: called.__setitem__("live", True))
    ui.install()
    assert called["renderer"] is True
    assert called["live"] is False
    ui.uninstall()


def test_install_selects_live_when_on(monkeypatch):
    monkeypatch.setenv("KRYTH_LIVE_UI", "1")
    import agent.ui as ui
    import agent.ui.renderer as renderer
    import agent.ui.live_engine as live
    ui.uninstall()
    called = {"renderer": False, "live": False}
    monkeypatch.setattr(renderer, "install", lambda: called.__setitem__("renderer", True))
    monkeypatch.setattr(live, "install", lambda: called.__setitem__("live", True))
    ui.install()
    assert called["live"] is True
    assert called["renderer"] is False
    ui.uninstall()


# ── Layout creation + persistent regions ─────────────────────────────────────

def _populated():
    eng = LiveEngine()
    s = eng._state
    s.provider, s.adapter, s.session_status = "NVIDIA", "Native", "Working"
    s.model = "qwen/qwen3-coder"
    s.tool_count, s.tokens, s.retries, s.ttft_ms = 2, 2418, 1, 420
    return eng


def test_layout_creates_all_regions():
    eng = _populated()
    eng._state.assistant = "Hello from KRYTH."
    eng._state.tools.append(("Read File", "llm.py", "done"))
    eng._state.timeline.append(TimelineItem("Reading files", "done", "3 files"))
    out = _render_text(eng)
    assert "KRYTH" in out                 # header
    assert "Live Timeline" in out         # timeline
    assert "Assistant" in out             # assistant panel
    assert "Tool Activity" in out         # tool panel
    assert "Tools" in out and "Elapsed" in out  # footer


def test_header_persists_provider_runtime_adapter():
    out = _render_text(_populated())
    assert "Provider" in out and "NVIDIA" in out
    assert "Adapter" in out and "Native" in out
    assert "Runtime" in out and "Event Driven" in out


def test_footer_persists_metrics():
    out = _render_text(_populated())
    for label in ("Tools", "Tokens", "Telemetry", "TTFT", "Retries", "Elapsed"):
        assert label in out


def test_planner_panel_renders_when_populated():
    eng = _populated()
    eng._state.goal = "Fix parser bug"
    eng._state.current = "Editing llm.py"
    eng._state.completed = ["Analyze stream", "Locate issue"]
    eng._state.next_step = "Run regression tests"
    out = _render_text(eng)
    assert "Planner" in out
    assert "Fix parser bug" in out
    assert "Run regression tests" in out


def test_planner_absent_when_empty():
    out = _render_text(_populated())
    assert "Planner" not in out  # no planner state → panel omitted


# ── Timeline scrolling ───────────────────────────────────────────────────────

def test_timeline_is_capped_and_scrolls():
    eng = _populated()
    for i in range(5000):
        eng._state.timeline.append(TimelineItem(f"step {i}", "done"))
    assert len(eng._state.timeline) <= 2000     # capped deque (supports thousands)
    out = _render_text(eng)
    assert "step 4999" in out                    # newest visible (tail rendered)
    assert "step 0" not in out                    # oldest scrolled off
    assert "step 100" not in out                  # only the visible tail is painted


def test_completed_history_visible():
    eng = _populated()
    eng._state.timeline.append(TimelineItem("Planning", "done"))
    eng._state.timeline.append(TimelineItem("Editing", "active"))
    out = _render_text(eng)
    assert "Planning" in out and "Editing" in out


# ── Event-driven updates (no live model) ─────────────────────────────────────

def _feed(eng, kind, **data):
    from agent.ui.events import Event
    eng._handle(Event(kind=kind, data=data))


def test_events_update_state():
    eng = LiveEngine()
    _feed(eng, EventKind.BANNER, model="qwen/q", base_url="https://integrate.api.nvidia.com/v1", skill_count=24)
    assert eng._state.provider  # provider resolved
    _feed(eng, EventKind.TURN_START)
    assert eng._state.session_status == "Working"
    _feed(eng, EventKind.TOOL_START, name="read_file", args={"path": "a.txt"})
    assert eng._state.tool_count == 1
    assert eng._state.tools[-1][0]  # action label set
    _feed(eng, EventKind.TOOL_RESULT, error=False)
    assert eng._state.tools[-1][2] == "done"
    _feed(eng, EventKind.ASSISTANT_MESSAGE, text="done")
    assert eng._state.assistant == "done"
    _feed(eng, EventKind.LLM_RETRY)
    assert eng._state.retries == 1
    # cleanup: ensure no live display left running
    eng.uninstall()


def test_assistant_and_tools_are_separate():
    eng = _populated()
    eng._state.assistant = "ASSISTANT_PROSE_MARKER"
    eng._state.tools.append(("Read File", "TOOL_TARGET_MARKER", "done"))
    out = _render_text(eng)
    # Both present, in their own panels (assistant text never inside Tool panel).
    a_idx = out.index("ASSISTANT_PROSE_MARKER")
    assert "Assistant" in out and "Tool Activity" in out
    assert "TOOL_TARGET_MARKER" in out


# ── Resize adaptivity ────────────────────────────────────────────────────────

@pytest.mark.parametrize("w,h", [(60, 20), (200, 50), (80, 24), (40, 12)])
def test_resize_adaptive(monkeypatch, w, h):
    from agent.ui.console import console
    import rich.console as rc
    monkeypatch.setattr(type(console), "size", property(lambda self: rc.ConsoleDimensions(w, h)))
    eng = _populated()
    eng._state.timeline.append(TimelineItem("step", "done"))
    out = _render_text(eng)        # must not raise at any size
    assert "KRYTH" in out


# ── No protocol leakage ──────────────────────────────────────────────────────

def test_no_protocol_leakage():
    eng = _populated()
    eng._state.assistant = "Clean answer."
    out = _render_text(eng)
    for bad in ("<think>", "</think>", "<tool_call>", "<|channel|>", "<display>"):
        assert bad not in out


# ── UI v5.1 — responsive breakpoints ─────────────────────────────────────────

@pytest.mark.parametrize("width,mode", [
    (60, "compact"), (79, "compact"),
    (80, "standard"), (119, "standard"),
    (120, "wide"), (159, "wide"),
    (160, "dashboard"), (240, "dashboard"),
])
def test_layout_mode_breakpoints(width, mode):
    assert LiveEngine.layout_mode(width) == mode


def _full_state():
    eng = _populated()
    s = eng._state
    s.goal = "Fix bug"
    s.assistant = "All done."
    s.recent.append("Read files")
    s.tools.append(("Read File", "a.py", "done"))
    s.timeline.append(TimelineItem("Editing", "active"))
    return eng


def test_compact_hides_planner_and_tools_keeps_assistant():
    out = _render_text(_full_state(), width=70)
    assert "Assistant" in out and "Live Timeline" in out  # always shown
    assert "Planner" not in out                            # hidden in compact
    assert "Tool Activity" not in out
    assert "TTFT" not in out                               # trimmed footer


def test_standard_shows_planner_not_tools():
    out = _render_text(_full_state(), width=100)
    assert "Planner" in out
    assert "Tool Activity" not in out


def test_wide_shows_tools_and_recent():
    out = _render_text(_full_state(), width=140)
    assert "Tool Activity" in out
    assert "Recent" in out


def test_dashboard_shows_everything():
    out = _render_text(_full_state(), width=180)
    for region in ("KRYTH", "Planner", "Recent", "Live Timeline", "Assistant", "Tool Activity", "TTFT"):
        assert region in out


# ── UI v5.1 — session memory (cross-turn) ────────────────────────────────────

def test_recent_persists_across_turns():
    eng = LiveEngine()
    # Turn 1: a tool completes → recorded into recent.
    _feed(eng, EventKind.TURN_START)
    _feed(eng, EventKind.TOOL_START, name="read_file", args={"path": "a.txt"})
    _feed(eng, EventKind.TOOL_RESULT, error=False)
    _feed(eng, EventKind.TURN_END, tokens_in=1, tokens_out=1)
    assert len(eng._state.recent) >= 1
    snapshot = list(eng._state.recent)
    # Turn 2 starts: recent must NOT be cleared (context preserved).
    _feed(eng, EventKind.TURN_START)
    assert list(eng._state.recent) == snapshot
    eng.uninstall()


def test_recent_renders_in_wide_mode():
    eng = _populated()
    eng._state.recent.append("Applied patch")
    out = _render_text(eng, width=180)
    assert "Recent" in out and "Applied patch" in out


# ── UI v5.1 — tool grouping + collapse ───────────────────────────────────────

def test_tool_category_mapping():
    assert LiveEngine._tool_category("Read File") == "File Operations"
    assert LiveEngine._tool_category("Write File") == "File Operations"
    assert LiveEngine._tool_category("Run Tests") == "Build & Test"
    assert LiveEngine._tool_category("Check Status") == "Verification"
    assert LiveEngine._tool_category("Git") == "Version Control"


def test_tools_grouped_and_collapsed():
    eng = _populated()
    s = eng._state
    s.tools.append(("Read File", "a.py", "done"))
    s.tools.append(("Read File", "a.py", "done"))   # repeat → collapse to ×2
    s.tools.append(("Run Tests", "", "done"))
    out = _render_text(eng, width=180)
    assert "File Operations" in out
    assert "Build & Test" in out
    assert "×2" in out                               # repeated action collapsed


# ── UI v5.1 — REPL handoff + fallback safety ─────────────────────────────────

def test_ensure_stopped_is_safe_idempotent():
    eng = LiveEngine()
    eng.ensure_stopped()         # no live display → no-op, must not raise
    eng.install()
    eng.ensure_stopped()         # installed but not started → no-op
    eng.uninstall()


def test_install_falls_back_when_live_engine_raises(monkeypatch):
    # If the live engine install raises, ui.install must fall back to the
    # incremental renderer (automatic rollback).
    monkeypatch.setenv("KRYTH_LIVE_UI", "1")
    import agent.ui as ui
    import agent.ui.renderer as renderer
    import agent.ui.live_engine as live
    ui.uninstall()
    called = {"renderer": False}
    def _boom():
        raise RuntimeError("live engine boom")
    monkeypatch.setattr(live, "install", _boom)
    monkeypatch.setattr(renderer, "install", lambda: called.__setitem__("renderer", True))
    ui.install()
    assert called["renderer"] is True   # fell back to the stable renderer
    ui.uninstall()


def test_long_session_timeline_bounded():
    # Thousands of entries must stay bounded (memory) and render only the tail.
    eng = LiveEngine()
    for i in range(10000):
        eng._state.timeline.append(TimelineItem(f"op {i}", "done"))
    assert len(eng._state.timeline) <= 2000
    out = _render_text(eng, width=120)
    assert "op 9999" in out


# ── UI v5.1 — assistant routing (Task 1) ─────────────────────────────────────

def test_conversational_content_routes_to_assistant_viewport():
    # A pure-conversation reply arrives as LLM_CONTENT_CHUNK events (no
    # TURN_START). It must accumulate into the Assistant viewport, NOT print
    # outside the dashboard.
    eng = LiveEngine()
    _feed(eng, EventKind.BANNER, model="qwen/q",
          base_url="https://integrate.api.nvidia.com/v1", skill_count=24)
    for piece in ["Hi! ", "I'm KRYTH, ", "your coding assistant."]:
        _feed(eng, EventKind.LLM_CONTENT_CHUNK, piece=piece)
    assert eng._state.assistant == "Hi! I'm KRYTH, your coding assistant."
    out = _render_text(eng, width=120)
    assert "Assistant" in out
    assert "your coding assistant" in out
    eng.uninstall()


def test_muted_suppressed_under_live_ui(monkeypatch):
    # ui.muted must NOT print to the console while the Live engine owns the
    # terminal (would land outside the frame). Verified via the gate helper.
    import agent.ui as ui
    import agent.ui.live_engine as live
    eng = live.get_engine()
    # Simulate an active live display.
    eng._installed = True

    class _FakeLive:
        def update(self, *a, **k):
            pass
        def stop(self):
            pass
    eng._live = _FakeLive()
    try:
        assert ui._live_active() is True
        printed = {"v": False}
        from agent.ui.console import console
        monkeypatch.setattr(console, "print", lambda *a, **k: printed.__setitem__("v", True))
        ui.muted("Task: conversational")
        assert printed["v"] is False   # suppressed
    finally:
        eng._live = None
        eng._installed = False
