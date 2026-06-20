"""Runtime v2 — spec-complete event bus contract.

The event-driven runtime exposes the canonical event vocabulary the spec
enumerates: assistant_message, tool_arguments, tool_finish, complete. These are
first-class EventKinds with ui.* emitters and renderer handlers, additive to the
existing granular events (which stay the primary emit path).
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agent.ui.events import EventKind, BUS  # noqa: E402
import agent.ui as ui  # noqa: E402


REQUIRED = {
    "ASSISTANT_MESSAGE": "assistant.message",
    "TOOL_ARGUMENTS": "tool.arguments",
    "TOOL_FINISH": "tool.finish",
    "COMPLETE": "runtime.complete",
}


@pytest.mark.parametrize("name,value", REQUIRED.items())
def test_event_kind_exists(name, value):
    assert hasattr(EventKind, name)
    assert getattr(EventKind, name).value == value


def _capture(fn):
    got = []
    unsub = BUS.subscribe(lambda e: got.append(e))
    try:
        fn()
    finally:
        unsub()
    return got


def test_assistant_message_emitter():
    evts = _capture(lambda: ui.assistant_message("the build passed"))
    e = next(e for e in evts if e.kind == EventKind.ASSISTANT_MESSAGE)
    assert e.data["text"] == "the build passed"


def test_tool_arguments_emitter():
    evts = _capture(lambda: ui.tool_arguments("read_file", {"path": "a.txt"}))
    e = next(e for e in evts if e.kind == EventKind.TOOL_ARGUMENTS)
    assert e.data["tool"] == "read_file"
    assert e.data["arguments"] == {"path": "a.txt"}


def test_tool_finish_emitter():
    evts = _capture(lambda: ui.tool_finish("read_file", ok=True))
    e = next(e for e in evts if e.kind == EventKind.TOOL_FINISH)
    assert e.data["tool"] == "read_file"
    assert e.data["ok"] is True


def test_complete_emitter():
    evts = _capture(lambda: ui.complete("done", 3))
    e = next(e for e in evts if e.kind == EventKind.COMPLETE)
    assert e.data["status"] == "done"
    assert e.data["turns_used"] == 3


def test_renderer_handlers_registered():
    import agent.ui.renderer as r
    for name in REQUIRED:
        assert EventKind[name] in r._HANDLERS


def test_renderer_consumes_without_error():
    # Install the real renderer and emit each event — must not raise.
    import agent.ui.renderer as r
    r.install()
    try:
        ui.assistant_message("x")
        ui.tool_arguments("read_file", {"path": "a"})
        ui.tool_finish("read_file", True)
        ui.complete("done", 1)
    finally:
        r.uninstall()


def test_emitters_exported():
    for fn in ("assistant_message", "tool_arguments", "tool_finish", "complete"):
        assert fn in ui.__all__
        assert callable(getattr(ui, fn))
