"""Tests for the rotating startup tips system."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from config.config import Config
from ui.components.tips import (
    StartupTips,
    Tip,
    TIP_REGISTRY,
    render_tip,
    render_tip_panel,
    select_tips,
)


# ---------------------------------------------------------------------------
# Tip dataclass
# ---------------------------------------------------------------------------


def test_tip_creation():
    tip = Tip(
        id="chat",
        title="Chat",
        description="Just type your message.",
        shortcut="Enter",
        category="navigation",
    )
    assert tip.id == "chat"
    assert tip.title == "Chat"
    assert tip.description == "Just type your message."
    assert tip.shortcut == "Enter"
    assert tip.category == "navigation"
    assert tip.condition is None


def test_tip_with_condition():
    def cond(config):
        return True

    tip = Tip(id="x", title="X", description="Y", condition=cond)
    assert tip.condition is cond


# ---------------------------------------------------------------------------
# Tip registry
# ---------------------------------------------------------------------------


def test_registry_has_tips():
    assert len(TIP_REGISTRY) > 0
    ids = {tip.id for tip in TIP_REGISTRY}
    assert "chat" in ids
    assert "slash-picker" in ids
    assert "execution-mode" in ids
    assert "cycle-mode" in ids
    assert "approval" in ids
    assert "compact" in ids
    assert "resume" in ids
    assert "thinking" in ids
    assert "interrupt" in ids


# ---------------------------------------------------------------------------
# Tip selection
# ---------------------------------------------------------------------------


def test_select_tips_returns_all_by_default():
    config = Config()
    tips = select_tips(config)
    assert len(tips) == len(TIP_REGISTRY)


def test_select_tips_filters_by_condition():
    def only_chat(config):
        return False  # nothing eligible

    config = Config()
    # Build a registry where all tips have a condition that returns False
    from ui.components import tips as tips_module

    original = tips_module.TIP_REGISTRY
    try:
        tips_module.TIP_REGISTRY = [
            Tip(id="a", title="A", description="a", condition=only_chat),
            Tip(id="b", title="B", description="b", condition=only_chat),
        ]
        assert select_tips(config) == []
    finally:
        tips_module.TIP_REGISTRY = original


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_tip_produces_text():
    tip = Tip(id="chat", title="Chat", description="Just type.", shortcut="Enter", category="navigation")
    text = render_tip(tip, 0, 1)
    assert "Just type." in text.plain
    assert "[Enter]" in text.plain
    assert "navigation" in text.plain


def test_render_tip_shows_index_when_multiple():
    tip = Tip(id="chat", title="Chat", description="Just type.")
    text = render_tip(tip, 0, 3)
    assert "1/3" in text.plain


def test_render_tip_omits_index_when_single():
    tip = Tip(id="chat", title="Chat", description="Just type.")
    text = render_tip(tip, 0, 1)
    assert "1/1" not in text.plain


def test_render_tip_panel():
    tip = Tip(id="chat", title="Chat", description="Just type.")
    panel = render_tip_panel(tip, 0, 1)
    assert isinstance(panel, SimpleNamespace) or hasattr(panel, "renderable")
    assert panel.title == "KRYTH"


# ---------------------------------------------------------------------------
# StartupTips
# ---------------------------------------------------------------------------


def test_startup_tips_init():
    console = SimpleNamespace(width=80, is_terminal=True)
    config = Config()
    tips = StartupTips(console, config)
    assert tips.tips is not None
    assert len(tips.tips) > 0
    assert tips.current_tip is not None
    assert tips._index == 0


def test_startup_tips_rotation(monkeypatch):
    """Tips advance when startup_tip_interval elapses."""

    import time

    console = SimpleNamespace(width=80, is_terminal=True)
    config = Config(startup_tip_interval=3.0)
    tips = StartupTips(console, config)

    first_tip = tips.current_tip
    assert tips._index == 0

    # Monkeypatch time.monotonic to simulate 4 seconds passing
    fake_time = [0.0]
    original_monotonic = time.monotonic

    def fake_monotonic():
        return fake_time[0]

    with patch.object(time, "monotonic", fake_monotonic):
        # First render at t=0
        tips.render()
        assert tips._index == 0

        # Advance past the interval
        fake_time[0] = 4.0
        tips.render()
        assert tips._index == 1


def test_startup_tips_no_tips():
    console = SimpleNamespace(width=80, is_terminal=True)
    config = Config()
    tips = StartupTips(console, config)
    tips.tips = []
    panel = tips.render()
    assert panel is not None


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_config_show_startup_tips_default_true():
    config = Config()
    assert config.ui.show_startup_tips is True


def test_config_startup_tip_interval_default():
    config = Config()
    assert config.ui.startup_tip_interval == 3.0


def test_config_can_disable_startup_tips():
    config = Config()
    config.ui.show_startup_tips = False
    assert config.ui.show_startup_tips is False


def test_config_can_set_tip_interval():
    config = Config()
    config.ui.startup_tip_interval = 5.0
    assert config.ui.startup_tip_interval == 5.0
