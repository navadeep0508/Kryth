"""Runtime v2 — flagged ModelAdapter normalization seam.

When KRYTH_ADAPTER_STREAM is set, ask_llm_stream parses each streaming chunk
through the provider-agnostic ModelAdapter instead of the inline manual delta
parsing. The surrounding orchestration is unchanged. These tests drive
ask_llm_stream with a synthetic OpenAI-shaped stream and prove the seam is
behavior-preserving: flag OFF and flag ON yield identical results.
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


# ── Synthetic OpenAI-shaped streaming chunks ────────────────────────────────

def _delta(content=None, reasoning=None, tool_calls=None):
    return types.SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )


def _chunk(delta=None, finish=None, usage=None):
    choice = types.SimpleNamespace(delta=delta or _delta(), finish_reason=finish)
    return types.SimpleNamespace(choices=[choice], usage=usage)


def _tool_delta(index, call_id, name, args):
    return types.SimpleNamespace(
        index=index, id=call_id,
        function=types.SimpleNamespace(name=name, arguments=args),
    )


def _text_stream():
    return [
        _chunk(_delta(content="Hello ")),
        _chunk(_delta(content="world."),),
        _chunk(usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=4)),
        _chunk(finish="stop"),
    ]


def _tool_stream(name="read_file", args='{"path": "a.txt"}'):
    return [
        _chunk(_delta(tool_calls=[_tool_delta(0, "call_1", name, args)])),
        _chunk(finish="tool_calls",
               usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=6)),
    ]


def _run(chunks, *, adapter, monkeypatch):
    """Run ask_llm_stream over a fixed synthetic stream, flag on/off."""
    if adapter:
        monkeypatch.setenv("KRYTH_ADAPTER_STREAM", "1")
    else:
        monkeypatch.delenv("KRYTH_ADAPTER_STREAM", raising=False)
    # Fixed model, no network model routing.
    monkeypatch.setattr(llm, "pick_main_model", lambda *a, **k: "openai/gpt-4o", raising=False)
    # Replace the real network call with our synthetic stream.
    monkeypatch.setattr(llm, "_chat_completion_with_compat",
                        lambda *a, **k: iter(list(chunks)))
    # Silence UI.
    import agent.ui as ui
    for n in dir(ui):
        f = getattr(ui, n, None)
        if callable(f) and not n.startswith("_"):
            try:
                monkeypatch.setattr(ui, n, lambda *a, **k: None)
            except Exception:
                pass
    return llm.ask_llm_stream([{"role": "user", "content": "hi"}], tools=None)


def _summary(res):
    """Comparable projection of the response."""
    tcs = res.get("tool_calls") or []
    norm_tcs = [
        (tc.get("function", {}).get("name"),
         tc.get("function", {}).get("arguments"))
        for tc in tcs
    ]
    usage = res.get("usage") or {}
    return {
        "content": (res.get("content") or "").strip(),
        "tool_calls": norm_tcs,
        "finish_reason": res.get("finish_reason"),
        "usage_in": usage.get("prompt_tokens"),
        "usage_out": usage.get("completion_tokens"),
    }


# ── Equivalence: flag OFF == flag ON ────────────────────────────────────────

@pytest.mark.parametrize("stream_fn", [_text_stream, _tool_stream])
def test_seam_is_behavior_preserving(stream_fn, monkeypatch):
    off = _summary(_run(stream_fn(), adapter=False, monkeypatch=monkeypatch))
    on = _summary(_run(stream_fn(), adapter=True, monkeypatch=monkeypatch))
    assert off == on, f"adapter seam diverged:\n off={off}\n on ={on}"


def test_text_content_assembled(monkeypatch):
    res = _run(_text_stream(), adapter=True, monkeypatch=monkeypatch)
    assert "Hello world." in (res.get("content") or "")


def test_tool_call_parsed_via_adapter(monkeypatch):
    res = _run(_tool_stream(), adapter=True, monkeypatch=monkeypatch)
    tcs = res.get("tool_calls") or []
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "read_file"


def test_harmony_contaminated_tool_name_via_seam(monkeypatch):
    # Even through the adapter seam, a Harmony-bled name resolves to the tool.
    res = _run(_tool_stream(name="read_file<|channel|>json"),
               adapter=True, monkeypatch=monkeypatch)
    tcs = res.get("tool_calls") or []
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "read_file"


def test_empty_stream_graceful_both_ways(monkeypatch):
    off = _summary(_run([_chunk(finish="stop")], adapter=False, monkeypatch=monkeypatch))
    on = _summary(_run([_chunk(finish="stop")], adapter=True, monkeypatch=monkeypatch))
    assert off == on


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("KRYTH_ADAPTER_STREAM", raising=False)
    assert llm._adapter_stream_enabled() is False
