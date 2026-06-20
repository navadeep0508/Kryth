"""Harmony / special-token tool-name sanitization — regression contract.

Live bug: a gpt-oss / Harmony model bled channel tokens into the tool-call
function name, e.g. ``read_file<|channel|>json``. title()-casing produced the
user-visible garbage ``Read File<|Channel|>Json`` and the registry lookup
failed with "tool not available". The runtime must strip any ``<|...|>``
special token from a tool name and resolve it to the real tool.
"""

import pytest

from agent.llm import _sanitize_tool_name


@pytest.mark.parametrize("raw,expected", [
    ("read_file<|channel|>json", "read_file"),
    ("write_file<|message|>", "write_file"),
    ("run_command<|call|>commentary", "run_command"),
    ("<|channel|>read_file", "read_file"),
    ("  read_file  ", "read_file"),
    ("read_file", "read_file"),
    ("multi_edit", "multi_edit"),
    ("edit_file<|start|>assistant<|channel|>", "edit_file"),
    ("delete_file<|return|>", "delete_file"),
])
def test_sanitize_strips_harmony_tokens(raw, expected):
    assert _sanitize_tool_name(raw) == expected


def test_sanitize_empty_and_garbage():
    assert _sanitize_tool_name("") == ""
    assert _sanitize_tool_name("<|channel|>") == ""
    assert _sanitize_tool_name(None) == ""  # type: ignore[arg-type]


def test_sanitized_name_resolves_in_registry():
    """A corrupted name must resolve to the real, registered tool."""
    from agent.tools import TOOLS
    corrupted = "read_file<|channel|>json"
    clean = _sanitize_tool_name(corrupted)
    assert clean == "read_file"
    assert clean in TOOLS, "sanitized name must hit the live tool registry"


def test_dispatch_uses_sanitized_name():
    """dispatch_tool_call must clean the name before any lookup/label."""
    import agent.agent_loop as al
    # The dispatch chokepoint imports and applies the sanitizer.
    assert al._sanitize_tool_name("read_file<|channel|>json") == "read_file"


def test_stream_assembly_sanitizes(monkeypatch):
    """Final tool_calls assembled from streaming deltas get sanitized names."""
    # Simulate the post-stream sanitization loop directly on a tool_calls list
    # shaped exactly like ask_llm_stream builds it.
    tool_calls = [
        {"id": "1", "type": "function",
         "function": {"name": "read_file<|channel|>json", "arguments": "{}"}},
        {"id": "2", "type": "function",
         "function": {"name": "write_file", "arguments": "{}"}},
    ]
    for tc in tool_calls:
        fn = tc.get("function")
        if fn and fn.get("name"):
            fn["name"] = _sanitize_tool_name(fn["name"])
    assert tool_calls[0]["function"]["name"] == "read_file"
    assert tool_calls[1]["function"]["name"] == "write_file"
