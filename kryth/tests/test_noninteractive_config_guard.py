"""Regression: prompt_config_fix must never block on input() in a
non-interactive session (piped stdin / redirected stdout / CI / harness).

Found during live validation: a 401/auth error triggered an interactive
"Open config to fix? [Y/n]" prompt whose input() hung forever when stdin was
not a TTY, freezing automated runs.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from kryth import config as cfg


class _FakeStdin:
    def __init__(self, tty):
        self._tty = tty
    def isatty(self):
        return self._tty


def test_noninteractive_does_not_call_input(monkeypatch):
    # stdin is not a TTY → must NOT reach input() and must return promptly.
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    called = {"input": False, "open_config": False}

    def _boom(*a, **k):
        called["input"] = True
        raise AssertionError("input() must not be called when non-interactive")

    monkeypatch.setattr("builtins.input", _boom)
    monkeypatch.setattr(cfg, "open_config_tui", lambda *a, **k: called.__setitem__("open_config", True))

    cfg.prompt_config_fix("AuthenticationError")  # must return, not hang/raise
    assert called["input"] is False
    assert called["open_config"] is False


def test_interactive_path_still_prompts(monkeypatch):
    # stdin IS a TTY → the prompt path runs; "n" declines without opening TUI.
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    opened = {"v": False}
    monkeypatch.setattr(cfg, "open_config_tui", lambda *a, **k: opened.__setitem__("v", True))
    cfg.prompt_config_fix("AuthenticationError")
    assert opened["v"] is False  # declined


def test_interactive_yes_opens_config(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    opened = {"v": False}
    monkeypatch.setattr(cfg, "open_config_tui", lambda *a, **k: opened.__setitem__("v", True))
    cfg.prompt_config_fix("AuthenticationError")
    assert opened["v"] is True
