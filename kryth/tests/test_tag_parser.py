"""Tests for the TagParser streaming state machine — Runtime v2 semantics.

The UI tag protocol is retired. The parser is now a leak-stripping safety net:
  • Reasoning tags (<think>, <thinking>, <reasoning>, …) are SILENT — their
    content is consumed and never shown (chain-of-thought is never exposed).
  • Native-tool-call XML internals (<tool_call>, <function=…>, <parameter=…>,
    <invoke>, …) are SILENT — consumed and hidden.
  • EVERYTHING ELSE is plain text passthrough — including former display tags
    like <plan>, <warning>, <status>, <display>. They are NOT parsed and are
    shown verbatim (the model is instructed never to emit them).
"""

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


def _kinds(events):
    return [e.kind for e in events]


# ── Reasoning is consumed and hidden (never exposed) ────────────────────────

def test_think_block_is_silent_and_hidden():
    events, state = _parse("<think>\nI should check.\n</think>\nHere is my answer.")
    # No visible block events — reasoning is silently consumed.
    assert EventKind.BLOCK_OPEN not in _kinds(events)
    assert EventKind.BLOCK_TEXT not in _kinds(events)
    assert state.mode == _Mode.NORMAL
    normal = _normal_text(events)
    assert "Here is my answer" in normal
    assert "I should check" not in normal      # reasoning hidden
    assert "<think>" not in normal
    assert "</think>" not in normal


def test_think_block_split_across_chunks_hidden():
    events, state = _parse_chunks("<think>", "reasoning here.", "</think>", "Final answer.")
    assert state.mode == _Mode.NORMAL
    normal = _normal_text(events)
    assert "Final answer" in normal
    assert "reasoning here" not in normal


def test_think_tag_split_across_chunk_boundary():
    events, state = _parse_chunks("hello <thi", "nk>reasoning</think>world")
    normal = _normal_text(events)
    assert "hello" in normal
    assert "world" in normal
    assert "reasoning" not in normal


def test_stream_ends_without_close_tag_stays_silent():
    events, state = _parse_chunks("<think>unfinished reasoning")
    # Unterminated reasoning is held in SILENT mode and never emitted.
    assert state.mode == _Mode.SILENT
    assert EventKind.BLOCK_TEXT not in _kinds(events)
    assert "unfinished" not in _normal_text(events)


def test_case_insensitive_reasoning_hidden():
    events, state = _parse("<THINK>reasoning</THINK>answer")
    assert "answer" in _normal_text(events)
    assert "reasoning" not in _normal_text(events)


def test_thinking_alt_tag_hidden():
    events, state = _parse("<thinking>internal</thinking>visible")
    assert "visible" in _normal_text(events)
    assert "internal" not in _normal_text(events)


def test_multiple_think_blocks_all_hidden():
    events, state = _parse("<think>first</think>between<think>second</think>end")
    normal = _normal_text(events)
    assert "between" in normal
    assert "end" in normal
    assert "first" not in normal
    assert "second" not in normal


# ── Tool-call XML internals are consumed and hidden ─────────────────────────

def test_tool_call_block_silent():
    events, state = _parse('before<tool_call>{"name": "write_file"}</tool_call>after')
    assert EventKind.BLOCK_OPEN not in _kinds(events)
    normal = _normal_text(events)
    assert "before" in normal
    assert "after" in normal
    assert "tool_call" not in normal
    assert "write_file" not in normal


def test_function_and_parameter_tags_silent():
    events, state = _parse(
        "before<function=write_file><parameter=path>foo.py</parameter></function>after"
    )
    normal = _normal_text(events)
    assert "before" in normal
    assert "after" in normal
    assert "write_file" not in normal
    assert "foo.py" not in normal


def test_orphan_closing_tag_discarded():
    events, state = _parse("clean text</think>more clean")
    assert EventKind.BLOCK_CLOSE not in _kinds(events)
    assert EventKind.BLOCK_OPEN not in _kinds(events)
    normal = _normal_text(events)
    assert "clean text" in normal
    assert "more clean" in normal
    assert "</think>" not in normal


# ── Former display tags now pass through as plain text ──────────────────────

def test_plan_tag_passthrough():
    # <plan> is no longer a protocol tag — it is shown verbatim as plain text.
    events, state = _parse("<plan>step 1\nstep 2</plan>result")
    assert _kinds(events) == [EventKind.NORMAL_TEXT]
    normal = _normal_text(events)
    assert "<plan>" in normal
    assert "step 1" in normal
    assert "result" in normal


def test_warning_tag_passthrough():
    events, state = _parse("<warning>careful here</warning>continue")
    assert _kinds(events) == [EventKind.NORMAL_TEXT]
    normal = _normal_text(events)
    assert "<warning>" in normal
    assert "careful here" in normal


def test_status_tag_passthrough():
    # A literal <status> (e.g. in a user's HTML question) is shown verbatim,
    # not eaten by a renderer.
    events, state = _parse("Use <status>200</status> for success")
    assert "<status>200</status>" in _normal_text(events)


# ── Plain text ──────────────────────────────────────────────────────────────

def test_plain_text_no_tags():
    events, state = _parse("This is a clean response with no tags.")
    assert _kinds(events) == [EventKind.NORMAL_TEXT]
    assert "clean response" in _normal_text(events)
    assert state.mode == _Mode.NORMAL


def test_reasoning_and_tool_xml_never_leak():
    """Reasoning + tool-XML must never appear in visible text; plain tags do."""
    raw = "<think>reason</think>answer <plan>kept</plan> done"
    events, _ = _parse(raw)
    normal = _normal_text(events)
    # Reasoning hidden:
    assert "<think>" not in normal and "reason" not in normal
    # Plain text + non-protocol tags shown:
    assert "answer" in normal and "done" in normal
    assert "<plan>" in normal
