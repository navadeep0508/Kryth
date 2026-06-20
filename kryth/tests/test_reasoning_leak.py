"""Regression: reasoning models that stream chain-of-thought in `content`.

Some reasoning models (e.g. stepfun step-3.7-flash on NVIDIA NIM) stream their
thinking INSIDE the content channel, terminated by a dangling </think> with NO
matching opening <think> (the open only appears in the separate reasoning_content
field). The balanced think-depth counter never engages, so the chain-of-thought
would leak into the visible reply. The runtime must treat the dangling close as
a reasoning→answer separator and surface ONLY the answer.
"""

import types
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import agent.llm as llm


# ── _filter_leaks safety net ────────────────────────────────────────────────

def test_filter_leaks_strips_dangling_reasoning_prefix():
    txt = "Let me think, greet back, keep it short. Yep.</think>\nHi there! How can I help?"
    out = llm._filter_leaks(txt)
    assert out == "Hi there! How can I help?"
    assert "think" not in out.lower()
    assert "Yep" not in out


def test_filter_leaks_handles_variants():
    for close in ("</think>", "</thinking>", "</reasoning>", "</seed:think>", "</analysis>"):
        txt = f"internal musings here{close}Answer."
        assert llm._filter_leaks(txt) == "Answer."


def test_filter_leaks_keeps_balanced_block_content():
    # A balanced <think>...</think> is stripped, surrounding answer kept.
    txt = "<think>secret</think>The answer is 42."
    out = llm._filter_leaks(txt)
    assert "42" in out
    assert "secret" not in out


def test_filter_leaks_leaves_clean_text_untouched():
    txt = "Hello! How can I help you build something today?"
    assert llm._filter_leaks(txt) == txt


def test_filter_leaks_no_false_positive_without_close():
    # Plain prose mentioning the word think must not be truncated.
    txt = "I think Python is a great choice for this."
    assert llm._filter_leaks(txt) == txt


# ── End-to-end through ask_llm_stream with a synthetic reasoning stream ──────

def _delta(content=None, reasoning=None):
    return types.SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)


def _chunk(delta=None, finish=None):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(delta=delta or _delta(), finish_reason=finish)], usage=None)


def _run(chunks, monkeypatch):
    monkeypatch.setattr(llm, "pick_main_model", lambda *a, **k: "stepfun-ai/step-3.7-flash", raising=False)
    monkeypatch.setattr(llm, "_chat_completion_with_compat", lambda *a, **k: iter(list(chunks)))
    import agent.ui as ui
    for n in dir(ui):
        f = getattr(ui, n, None)
        if callable(f) and not n.startswith("_"):
            try:
                monkeypatch.setattr(ui, n, lambda *a, **k: None)
            except Exception:
                pass
    return llm.ask_llm_stream([{"role": "user", "content": "hi"}], tools=None)


def test_dangling_think_in_content_is_hidden(monkeypatch):
    # Model streams reasoning in content, ends with </think>, then the answer.
    chunks = [
        _chunk(_delta(content="Okay, greet back, keep it short. ")),
        _chunk(_delta(content="Yep, that works.</think>\n")),
        _chunk(_delta(content="Hey there! I'm KRYTH — how can I help?")),
        _chunk(finish="stop"),
    ]
    res = _run(chunks, monkeypatch)
    content = (res.get("content") or "").strip()
    assert content == "Hey there! I'm KRYTH — how can I help?"
    assert "greet back" not in content
    assert "</think>" not in content


def test_reasoning_via_separate_field_then_clean_content(monkeypatch):
    # Reasoning on reasoning_content (hidden), content already clean.
    chunks = [
        _chunk(_delta(reasoning="thinking about the greeting")),
        _chunk(_delta(content="Hello! What can I build for you?")),
        _chunk(finish="stop"),
    ]
    res = _run(chunks, monkeypatch)
    assert (res.get("content") or "").strip() == "Hello! What can I build for you?"
