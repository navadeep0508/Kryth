"""Tests for the TagParser streaming state machine."""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from agent.ui.streaming import TagParser, TagParserState, EventKind, _Mode


def _parse(text: str):
    p = TagParser()
    s = TagParserState()
    return list(p.feed(text, s)), s


def _parse_chunks(*chunks: str):
    p = TagParser()
    s = TagParserState()
    events = []
    for chunk in chunks:
        events.extend(p.feed(chunk, s))
    return events, s


def _normal_text(events):
    return "".join(e.text for e in events if e.kind == EventKind.NORMAL_TEXT)


def _block_text(events):
    return "".join(e.text for e in events if e.kind == EventKind.BLOCK_TEXT)


def _kinds(events):
    return [e.kind for e in events]


# ── Parser tests ──────────────────────────────────────────────────────────────

def test_complete_think_block_one_chunk():
    events, state = _parse("<think>\nI should check.\n</think>\nHere is my answer.")
    assert EventKind.BLOCK_OPEN  in _kinds(events)
    assert EventKind.BLOCK_TEXT  in _kinds(events)
    assert EventKind.BLOCK_CLOSE in _kinds(events)
    assert EventKind.NORMAL_TEXT in _kinds(events)
    assert state.mode == _Mode.NORMAL
    assert "Here is my answer" in _normal_text(events)
    assert "I should check" in _block_text(events)
    assert "<think>" not in _normal_text(events)
    assert "</think>" not in _normal_text(events)


def test_think_block_split_across_chunks():
    events, state = _parse_chunks("<think>", "reasoning here.", "</think>", "Final answer.")
    assert EventKind.BLOCK_OPEN  in _kinds(events)
    assert EventKind.BLOCK_TEXT  in _kinds(events)
    assert EventKind.BLOCK_CLOSE in _kinds(events)
    assert EventKind.NORMAL_TEXT in _kinds(events)
    assert state.mode == _Mode.NORMAL
    assert "Final answer" in _normal_text(events)
    assert "reasoning here" in _block_text(events)


def test_orphan_closing_tag_discarded():
    events, state = _parse("clean text</think>more clean")
    assert EventKind.BLOCK_CLOSE not in _kinds(events)
    assert EventKind.BLOCK_OPEN  not in _kinds(events)
    normal = _normal_text(events)
    assert "clean text" in normal
    assert "more clean" in normal
    assert "</think>" not in normal


def test_tool_call_block_silent():
    events, state = _parse('before<tool_call>{"name": "write_file"}</tool_call>after')
    assert EventKind.BLOCK_OPEN  not in _kinds(events)
    assert EventKind.BLOCK_TEXT  not in _kinds(events)
    assert EventKind.BLOCK_CLOSE not in _kinds(events)
    normal = _normal_text(events)
    assert "before" in normal
    assert "after"  in normal
    assert "tool_call" not in normal
    assert "write_file" not in normal


def test_think_tag_split_across_chunk_boundary():
    events, state = _parse_chunks("hello <thi", "nk>reasoning</think>world")
    assert EventKind.BLOCK_OPEN  in _kinds(events)
    assert EventKind.BLOCK_CLOSE in _kinds(events)
    normal = _normal_text(events)
    assert "hello" in normal
    assert "world" in normal


def test_stream_ends_without_close_tag():
    events, state = _parse_chunks("<think>unfinished reasoning")
    assert state.mode == _Mode.IN_BLOCK
    assert EventKind.BLOCK_OPEN in _kinds(events)
    assert EventKind.BLOCK_TEXT in _kinds(events)
    assert EventKind.BLOCK_CLOSE not in _kinds(events)


def test_case_insensitive():
    events, state = _parse("<THINK>reasoning</THINK>answer")
    assert EventKind.BLOCK_OPEN  in _kinds(events)
    assert EventKind.BLOCK_CLOSE in _kinds(events)
    assert "answer" in _normal_text(events)


def test_plan_tag_extensibility():
    events, state = _parse("<plan>step 1\nstep 2</plan>result")
    assert EventKind.BLOCK_OPEN  in _kinds(events)
    assert EventKind.BLOCK_TEXT  in _kinds(events)
    assert EventKind.BLOCK_CLOSE in _kinds(events)
    assert "step 1" in _block_text(events)
    assert "result" in _normal_text(events)


def test_warning_tag():
    events, state = _parse("<warning>careful here</warning>continue")
    assert EventKind.BLOCK_OPEN  in _kinds(events)
    assert EventKind.BLOCK_TEXT  in _kinds(events)
    assert EventKind.BLOCK_CLOSE in _kinds(events)
    assert "careful here" in _block_text(events)
    assert "continue" in _normal_text(events)


def test_multiple_think_blocks():
    events, state = _parse("<think>first</think>between<think>second</think>end")
    opens  = [e for e in events if e.kind == EventKind.BLOCK_OPEN]
    closes = [e for e in events if e.kind == EventKind.BLOCK_CLOSE]
    assert len(opens)  == 2
    assert len(closes) == 2
    assert "between" in _normal_text(events)
    assert "end"     in _normal_text(events)


def test_plain_text_no_tags():
    events, state = _parse("This is a clean response with no tags.")
    assert _kinds(events) == [EventKind.NORMAL_TEXT]
    assert "clean response" in _normal_text(events)
    assert state.mode == _Mode.NORMAL


def test_function_and_parameter_tags_silent():
    events, state = _parse(
        "before<function=write_file><parameter=path>foo.py</parameter></function>after"
    )
    normal = _normal_text(events)
    assert "before" in normal
    assert "after"  in normal
    assert "write_file" not in normal
    assert "foo.py"     not in normal


def test_thinking_alt_tag():
    """<thinking> variant used by some models."""
    events, state = _parse("<thinking>internal</thinking>visible")
    assert EventKind.BLOCK_OPEN  in _kinds(events)
    assert EventKind.BLOCK_CLOSE in _kinds(events)
    assert "internal" in _block_text(events)
    assert "visible"  in _normal_text(events)


def test_no_tags_leaked_to_normal_text():
    """Ensure none of the raw tag strings appear in NORMAL_TEXT events."""
    raw = "<think>reason</think>answer with <plan>stuff</plan> done"
    events, _ = _parse(raw)
    normal = _normal_text(events)
    for tag in ("<think>", "</think>", "<plan>", "</plan>"):
        assert tag not in normal, f"Tag {tag!r} leaked into normal text"
