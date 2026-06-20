"""Runtime v2 — XML tag protocol retired. Contract tests.

Locks in the migration away from the model-emitted XML/tag protocol:
  • The system prompt no longer instructs models to emit UI tags, and forbids
    them explicitly.
  • The streaming renderer is a leak-stripping plain-text passthrough:
    reasoning + tool-call XML are consumed/hidden; everything else (including
    former display tags) is shown verbatim.
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


# ── System prompt no longer carries the tag protocol ────────────────────────

def test_system_prompt_has_no_tag_protocol():
    from agent.prompts import SYSTEM_PROMPT
    # Legacy protocol headers / instructions must be gone.
    for marker in (
        "UNIVERSAL UI TAG PROTOCOL",
        "APPROVED DISPLAY TAGS",
        "emit this as position-0 token",
        "accumulate-and-render",
        "Emit <CONTENT>",
    ):
        assert marker not in SYSTEM_PROMPT, f"legacy protocol marker present: {marker!r}"


def test_system_prompt_forbids_xml_tags():
    from agent.prompts import SYSTEM_PROMPT
    assert "NEVER emit XML or pseudo-XML" in SYSTEM_PROMPT
    assert "native function calling" in SYSTEM_PROMPT


def test_legacy_tag_protocol_constant_empty():
    from agent.prompts import KRYTH_TAG_PROTOCOL
    assert KRYTH_TAG_PROTOCOL == ""


# ── Streaming is plain-text passthrough + leak stripping ────────────────────

def _render(*pieces):
    from agent.ui import streaming as S
    from agent.ui.console import console
    sp = S.StreamPrinter()
    buf = io.StringIO()
    S._write_inplace = lambda t: buf.write(t)
    console.out = lambda t, **k: buf.write(t)
    sp.begin_content()
    for p in pieces:
        sp.content_chunk(p)
    sp.end_content()
    return re.sub(r"\x1b\[[0-9;]*m", "", buf.getvalue())


def test_plain_text_passthrough():
    out = _render("Hello, ", "the fix is done.")
    assert "Hello, the fix is done." in out


def test_reasoning_hidden():
    out = _render("<think>secret chain of thought</think>", "Answer: 42")
    assert "Answer: 42" in out
    assert "secret" not in out
    assert "<think>" not in out


def test_tool_call_xml_hidden():
    out = _render('<tool_call>{"name":"x"}</tool_call>', "ok")
    assert "ok" in out
    assert "tool_call" not in out


def test_harmony_channel_token_stripped():
    out = _render("Done<|channel|>commentary")
    assert "<|channel|>" not in out
    assert "Done" in out


def test_former_display_tag_shown_verbatim():
    # A literal <status> (e.g. in a user's HTML answer) is NOT eaten anymore.
    out = _render("Use <status>200</status> for success")
    assert "<status>200</status>" in out


def test_no_accumulate_render_tags_remain():
    from agent.ui.streaming import _ACC_TAGS, _TAG_RENDERERS
    assert _ACC_TAGS == frozenset()
    assert _TAG_RENDERERS == {}


def test_all_remaining_specs_are_silent():
    # Every surviving tag spec is a silent leak-stripper (nothing rendered live).
    from agent.ui.streaming import _TAG_SPECS
    assert all(s.show_live is False for s in _TAG_SPECS)


# ── Tool-parser: native + validated JSON only, no XML, Harmony-safe ─────────

def test_tool_parser_has_no_xml_method():
    from agent.model.tool_parser import ToolParser
    assert not hasattr(ToolParser, "from_xml_text")


def test_tool_parser_rejects_xml_and_malformed():
    from agent.model.tool_parser import ToolParser
    tp = ToolParser()
    assert tp.from_any("<tool_call><tool_name>x</tool_name></tool_call>") == []
    assert tp.from_any('{"arguments":{}}') == []                      # no name
    assert tp.from_any('{"name":"x","arguments":"str"}') == []        # bad args


def test_tool_parser_accepts_validated_json_and_sanitizes():
    from agent.model.tool_parser import ToolParser
    tp = ToolParser()
    calls = tp.from_any('{"name":"read_file<|channel|>json","arguments":{"path":"a"}}')
    assert len(calls) == 1
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "a"}
