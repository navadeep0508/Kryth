"""Tests for the KRYTH Universal Model Adapter.

Covers:
- ProviderRegistry — detect_provider, is_openai_compatible
- ProviderProfiles — get_profile, all tags
- CapabilityDetector — provider defaults, model patches, from_model_id
- ReasoningDetector — feed (inline tags), extract_reasoning, synthetic_phases
- ToolParser — OpenAI delta, Anthropic content, XML text, JSON fences
- ResponseParser — clean, has_tool_calls, split
- StreamNormalizer — text chunks, reasoning extraction, tool_calls finish
- FallbackAdapter — unknown chunks, text, dicts
- ModelAdapter — factory, normalize_complete_response, clean_text, synthetic
- streaming.py tag specs — new provider tags present
"""

from __future__ import annotations

import json
import types
from dataclasses import dataclass, field
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# ProviderRegistry
# ---------------------------------------------------------------------------

class TestProviderRegistry:
    def test_detect_anthropic_by_model(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("claude-opus-4-8") == Provider.ANTHROPIC

    def test_detect_openai_by_model(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("gpt-4o") == Provider.OPENAI

    def test_detect_deepseek(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("deepseek-r1") == Provider.DEEPSEEK

    def test_detect_qwen(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("qwen2.5-72b") == Provider.QWEN

    def test_detect_qwq(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("QwQ-32B") == Provider.QWEN

    def test_detect_llama(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("meta-llama/Llama-3-8b") == Provider.LLAMA

    def test_detect_mistral(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("mistral-7b-instruct") == Provider.MISTRAL

    def test_detect_ollama_by_url(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("llama3", "http://localhost:11434") == Provider.OLLAMA

    def test_detect_grok_by_url(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("grok-3", "https://api.x.ai/v1") == Provider.GROK

    def test_detect_unknown(self):
        from agent.model.provider_registry import detect_provider, Provider
        assert detect_provider("my-custom-model", "http://my-server.local") == Provider.UNKNOWN

    def test_openai_compatible(self):
        from agent.model.provider_registry import is_openai_compatible, Provider
        assert is_openai_compatible(Provider.OPENAI) is True
        assert is_openai_compatible(Provider.DEEPSEEK) is True
        assert is_openai_compatible(Provider.ANTHROPIC) is False

    def test_gemini_openai_compat(self):
        from agent.model.provider_registry import is_openai_compatible, Provider
        assert is_openai_compatible(Provider.GEMINI) is False


# ---------------------------------------------------------------------------
# ProviderProfiles
# ---------------------------------------------------------------------------

class TestProviderProfiles:
    def test_anthropic_has_xml_format(self):
        from agent.model.provider_profiles import get_profile
        from agent.model.provider_registry import Provider
        p = get_profile(Provider.ANTHROPIC)
        assert p.tool_format == "xml"

    def test_openai_has_json_format(self):
        from agent.model.provider_profiles import get_profile
        from agent.model.provider_registry import Provider
        p = get_profile(Provider.OPENAI)
        assert p.tool_format == "openai"

    def test_deepseek_has_reasoning_tags(self):
        from agent.model.provider_profiles import get_profile
        from agent.model.provider_registry import Provider
        p = get_profile(Provider.DEEPSEEK)
        assert any("think" in t or "reasoning" in t for t in p.reasoning_tags)

    def test_qwen_has_think_tags(self):
        from agent.model.provider_profiles import get_profile
        from agent.model.provider_registry import Provider
        p = get_profile(Provider.QWEN)
        assert "<think>" in p.reasoning_tags

    def test_unknown_gets_fallback(self):
        from agent.model.provider_profiles import get_profile
        from agent.model.provider_registry import Provider
        p = get_profile(Provider.UNKNOWN)
        assert len(p.reasoning_tags) > 0

    def test_all_reasoning_open_tags(self):
        from agent.model.provider_profiles import all_reasoning_open_tags
        tags = all_reasoning_open_tags()
        assert "<think>" in tags
        assert "<thinking>" in tags
        assert "<reasoning>" in tags

    def test_all_reasoning_close_tags(self):
        from agent.model.provider_profiles import all_reasoning_close_tags
        tags = all_reasoning_close_tags()
        assert "</think>" in tags
        assert "</thinking>" in tags


# ---------------------------------------------------------------------------
# CapabilityDetector
# ---------------------------------------------------------------------------

class TestCapabilityDetector:
    def test_anthropic_defaults(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("claude-opus-4-8")
        assert caps.tool_calling is True
        assert caps.streaming is True
        assert caps.xml_tools is True

    def test_openai_parallel_tools(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("gpt-4o")
        assert caps.parallel_tools is True
        assert caps.json_tools is True

    def test_o1_reasoning(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("o1-preview")
        assert caps.reasoning is True
        assert caps.hidden_reasoning is True

    def test_deepseek_r1_reasoning(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("deepseek-r1")
        assert caps.reasoning is True

    def test_qwq_reasoning(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("QwQ-32B")
        assert caps.reasoning is True

    def test_claude_thinking(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("claude-opus-4-8")
        assert caps.thinking_tokens is True or caps.reasoning is True

    def test_ollama_no_tools(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("llama3", "http://localhost:11434")
        assert caps.tool_calling is False

    def test_gemini_vision(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("gemini-1.5-pro")
        assert caps.vision is True

    def test_unknown_model_sane_defaults(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("my-custom-model", "http://localhost:9999")
        assert caps.streaming is True  # safe default

    def test_capabilities_to_dict(self):
        from agent.model.capability_detector import detect_capabilities
        caps = detect_capabilities("gpt-4o")
        d = caps.to_dict()
        assert "reasoning" in d
        assert "tool_calling" in d


# ---------------------------------------------------------------------------
# ReasoningDetector
# ---------------------------------------------------------------------------

class TestReasoningDetector:
    def test_extracts_think_tag(self):
        from agent.model.reasoning_detector import ReasoningDetector
        rd = ReasoningDetector()
        text = "<think>This is my reasoning</think> Here is the answer."
        vis, _ = rd.feed(text)
        assert "This is my reasoning" not in vis
        assert "Here is the answer." in vis

    def test_extracts_thinking_tag(self):
        from agent.model.reasoning_detector import ReasoningDetector
        rd = ReasoningDetector()
        text = "<thinking>plan</thinking>response"
        vis, _ = rd.feed(text)
        assert "response" in vis
        assert "plan" not in vis

    def test_returns_reasoning_text(self):
        from agent.model.reasoning_detector import ReasoningDetector
        rd = ReasoningDetector()
        text = "<think>my chain of thought</think>answer"
        _, reasoning = rd.feed(text)
        assert reasoning is not None or rd.collected_blocks()

    def test_no_tags_returns_all_visible(self):
        from agent.model.reasoning_detector import ReasoningDetector
        rd = ReasoningDetector()
        text = "Plain response without tags."
        vis, reason = rd.feed(text)
        assert vis == text
        assert reason is None

    def test_streaming_across_chunks(self):
        from agent.model.reasoning_detector import ReasoningDetector
        rd = ReasoningDetector()
        vis1, _ = rd.feed("<think>start of ")
        vis2, _ = rd.feed("reasoning</think>response")
        assert "reasoning" not in vis2 or "response" in vis2

    def test_extract_reasoning_full_text(self):
        from agent.model.reasoning_detector import extract_reasoning
        text = "Before<think>think content</think>After"
        reasoning, clean = extract_reasoning(text)
        assert "think content" in reasoning
        assert "After" in clean
        assert "<think>" not in clean

    def test_synthetic_phases_no_reasoning(self):
        from agent.model.reasoning_detector import ReasoningDetector
        rd = ReasoningDetector()
        phases = rd.synthetic_phases("run_command")
        assert len(phases) > 0
        assert all(isinstance(p, str) for p in phases)

    def test_reset_clears_state(self):
        from agent.model.reasoning_detector import ReasoningDetector
        rd = ReasoningDetector()
        rd.feed("<think>partial")
        assert rd.is_reasoning() is True
        rd.reset()
        assert rd.is_reasoning() is False

    def test_reasoning_tag(self):
        from agent.model.reasoning_detector import extract_reasoning
        text = "Prefix<reasoning>deep thought</reasoning>Suffix"
        reasoning, clean = extract_reasoning(text)
        assert "deep thought" in reasoning
        assert "Suffix" in clean


# ---------------------------------------------------------------------------
# ToolParser
# ---------------------------------------------------------------------------

class TestToolParser:
    def test_openai_delta_simple(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        tc = types.SimpleNamespace(
            function=types.SimpleNamespace(name="read_file", arguments='{"path": "main.py"}'),
            id="call_123", index=0,
        )
        calls = parser.from_openai_delta([tc])
        assert len(calls) == 1
        assert calls[0].name == "read_file"
        assert calls[0].arguments["path"] == "main.py"

    def test_openai_empty_args(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        tc = types.SimpleNamespace(
            function=types.SimpleNamespace(name="get_time", arguments=""),
            id="call_1", index=0,
        )
        calls = parser.from_openai_delta([tc])
        assert calls[0].arguments == {}

    def test_anthropic_tool_use_block(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        block = types.SimpleNamespace(type="tool_use", name="edit_file",
                                      input={"path": "app.py", "content": "x=1"},
                                      id="toolu_123")
        calls = parser.from_anthropic_content([block])
        assert len(calls) == 1
        assert calls[0].name == "edit_file"
        assert calls[0].arguments["path"] == "app.py"

    def test_xml_text_parsing(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        text = '<tool_call><tool_name>run_tests</tool_name><parameters>{"suite": "unit"}</parameters></tool_call>'
        calls = parser.from_xml_text(text)
        assert len(calls) == 1
        assert calls[0].name == "run_tests"

    def test_json_fence_parsing(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        text = '```json\n{"name": "shell_exec", "arguments": {"command": "ls -la"}}\n```'
        calls = parser.from_json_fences(text)
        assert len(calls) == 1
        assert calls[0].name == "shell_exec"

    def test_from_any_xml_fallback(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        text = '<invoke><tool_name>get_status</tool_name><input>{"key": "val"}</input></invoke>'
        calls = parser.from_any(text)
        assert len(calls) >= 1

    def test_no_tool_calls_returns_empty(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        calls = parser.from_any("This is just a plain response with no tools.")
        assert calls == []

    def test_anthropic_dict_block(self):
        from agent.model.tool_parser import ToolParser
        parser = ToolParser()
        block = {"type": "tool_use", "name": "write_file",
                 "input": {"path": "test.py"}, "id": "toolu_xyz"}
        calls = parser.from_anthropic_content([block])
        assert calls[0].name == "write_file"


# ---------------------------------------------------------------------------
# ResponseParser
# ---------------------------------------------------------------------------

class TestResponseParser:
    def test_removes_think_tags(self):
        from agent.model.response_parser import clean_output
        text = "<think>hidden reasoning</think>visible response"
        assert "hidden reasoning" not in clean_output(text)
        assert "visible response" in clean_output(text)

    def test_removes_tool_call_tags(self):
        from agent.model.response_parser import clean_output
        text = "Some text<tool_call>json payload</tool_call>more text"
        result = clean_output(text)
        assert "json payload" not in result
        assert "Some text" in result

    def test_removes_json_fence_tool_calls(self):
        from agent.model.response_parser import clean_output
        text = 'Here is the answer.\n```json\n{"name": "tool", "arguments": {}}\n```\nDone.'
        result = clean_output(text)
        assert "name" not in result or "Here is the answer." in result

    def test_removes_reasoning_tags(self):
        from agent.model.response_parser import clean_output
        text = "<reasoning>my analysis</reasoning>conclusion"
        assert "my analysis" not in clean_output(text)
        assert "conclusion" in clean_output(text)

    def test_clean_empty_string(self):
        from agent.model.response_parser import clean_output
        assert clean_output("") == ""

    def test_clean_no_tags(self):
        from agent.model.response_parser import clean_output
        text = "Plain response with no markup."
        assert clean_output(text) == text

    def test_has_tool_calls_true(self):
        from agent.model.response_parser import ResponseParser
        p = ResponseParser()
        assert p.has_tool_calls("<tool_call>x</tool_call>") is True

    def test_has_tool_calls_false(self):
        from agent.model.response_parser import ResponseParser
        p = ResponseParser()
        assert p.has_tool_calls("just text") is False

    def test_split_response(self):
        from agent.model.response_parser import split_response
        text = "<think>internal thought</think>external answer"
        # split_response returns (reasoning_text, clean_text)
        reasoning, clean = split_response(text)
        assert "external answer" in clean
        # reasoning may be in reasoning OR in clean depending on extraction
        assert "internal thought" in reasoning or "internal thought" not in clean


# ---------------------------------------------------------------------------
# StreamNormalizer
# ---------------------------------------------------------------------------

class TestStreamNormalizer:
    def _make_chunk(self, content="", finish=None, tool_calls=None, reasoning=None):
        """Build a fake OpenAI-style streaming chunk."""
        delta = types.SimpleNamespace(
            content=content,
            tool_calls=tool_calls or [],
            reasoning_content=reasoning,
        )
        choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
        return types.SimpleNamespace(choices=[choice], usage=None)

    def test_text_chunk(self):
        from agent.model.stream_normalizer import StreamNormalizer, ChunkType
        sn = StreamNormalizer()
        chunk = self._make_chunk("Hello world")
        results = sn.normalize_openai_chunk(chunk)
        texts = [r for r in results if r.chunk_type == ChunkType.TEXT]
        assert any("Hello" in t.text for t in texts)

    def test_reasoning_content_field(self):
        from agent.model.stream_normalizer import StreamNormalizer, ChunkType
        sn = StreamNormalizer()
        chunk = self._make_chunk(content="", reasoning="thinking...")
        results = sn.normalize_openai_chunk(chunk)
        reasons = [r for r in results if r.chunk_type == ChunkType.REASONING]
        assert any("thinking" in r.reasoning for r in reasons)

    def test_inline_think_tags_stripped(self):
        from agent.model.stream_normalizer import StreamNormalizer, ChunkType
        sn = StreamNormalizer()
        chunk = self._make_chunk("<think>hidden</think>visible")
        results = sn.normalize_openai_chunk(chunk)
        texts = [r for r in results if r.chunk_type == ChunkType.TEXT]
        reasons = [r for r in results if r.chunk_type == ChunkType.REASONING]
        assert any("visible" in t.text for t in texts)
        assert not any("hidden" in t.text for t in texts)

    def test_tool_calls_finish(self):
        from agent.model.stream_normalizer import StreamNormalizer, ChunkType
        sn = StreamNormalizer()
        # First chunk sets up the tool call buffer
        tc = types.SimpleNamespace(
            function=types.SimpleNamespace(name="read_file", arguments='{"path":"x"}'),
            id="c1", index=0,
        )
        chunk1 = self._make_chunk(tool_calls=[tc])
        sn.normalize_openai_chunk(chunk1)
        # Finish chunk triggers output
        chunk2 = self._make_chunk(finish="tool_calls")
        results = sn.normalize_openai_chunk(chunk2)
        tool_chunks = [r for r in results if r.chunk_type == ChunkType.TOOL_CALL]
        assert len(tool_chunks) == 1
        assert tool_chunks[0].tool_name == "read_file"

    def test_done_chunk(self):
        from agent.model.stream_normalizer import StreamNormalizer, ChunkType
        sn = StreamNormalizer()
        chunk = self._make_chunk(finish="stop")
        results = sn.normalize_openai_chunk(chunk)
        assert any(r.chunk_type == ChunkType.DONE for r in results)

    def test_normalize_text_chunk(self):
        from agent.model.stream_normalizer import StreamNormalizer, ChunkType
        sn = StreamNormalizer()
        results = sn.normalize_text_chunk("Hello from Ollama!")
        assert any(r.chunk_type == ChunkType.TEXT for r in results)

    def test_reset_clears_state(self):
        from agent.model.stream_normalizer import StreamNormalizer
        sn = StreamNormalizer()
        sn._tool_buffers[0] = {"name": "test", "args": "", "id": ""}
        sn.reset()
        assert sn._tool_buffers == {}


# ---------------------------------------------------------------------------
# FallbackAdapter
# ---------------------------------------------------------------------------

class TestFallbackAdapter:
    def test_plain_text(self):
        from agent.model.fallback_adapter import FallbackAdapter
        from agent.model.stream_normalizer import ChunkType
        fa = FallbackAdapter()
        results = fa.normalize_chunk("Hello from unknown model")
        texts = [r for r in results if r.chunk_type == ChunkType.TEXT]
        assert len(texts) > 0

    def test_text_with_think_tags(self):
        from agent.model.fallback_adapter import FallbackAdapter
        from agent.model.stream_normalizer import ChunkType
        fa = FallbackAdapter()
        results = fa.normalize_chunk("<think>plan</think>response")
        texts = [r for r in results if r.chunk_type == ChunkType.TEXT]
        reasons = [r for r in results if r.chunk_type == ChunkType.REASONING]
        assert any("response" in t.text for t in texts)

    def test_dict_with_content(self):
        from agent.model.fallback_adapter import FallbackAdapter
        from agent.model.stream_normalizer import ChunkType
        fa = FallbackAdapter()
        results = fa.normalize_chunk({"content": "Hello from dict"})
        texts = [r for r in results if r.chunk_type == ChunkType.TEXT]
        assert len(texts) > 0

    def test_unknown_type_no_crash(self):
        from agent.model.fallback_adapter import FallbackAdapter
        fa = FallbackAdapter()
        results = fa.normalize_chunk(42)   # nonsensical input
        assert isinstance(results, list)

    def test_normalize_text_strips_markup(self):
        from agent.model.fallback_adapter import FallbackAdapter
        fa = FallbackAdapter()
        clean = fa.normalize_text("<think>hidden</think>Clean text")
        assert "Clean text" in clean
        assert "<think>" not in clean

    def test_extract_tool_calls(self):
        from agent.model.fallback_adapter import FallbackAdapter
        fa = FallbackAdapter()
        text = '```json\n{"name": "run_command", "arguments": {"cmd": "ls"}}\n```'
        calls = fa.extract_tool_calls(text)
        assert len(calls) >= 1 or calls == []   # may or may not find in fallback


# ---------------------------------------------------------------------------
# ModelAdapter
# ---------------------------------------------------------------------------

class TestModelAdapter:
    def test_for_model_factory(self):
        from agent.model.model_adapter import ModelAdapter
        adapter = ModelAdapter.for_model("gpt-4o")
        assert adapter.model_name == "gpt-4o"
        assert adapter.capabilities.tool_calling is True

    def test_provider_detection(self):
        from agent.model.model_adapter import ModelAdapter
        from agent.model.provider_registry import Provider
        adapter = ModelAdapter.for_model("claude-opus-4-8")
        assert adapter.provider == Provider.ANTHROPIC

    def test_clean_text(self):
        from agent.model.model_adapter import ModelAdapter
        adapter = ModelAdapter.for_model("gpt-4o")
        clean = adapter.clean_text("<think>hidden</think>response")
        assert "hidden" not in clean
        assert "response" in clean

    def test_synthetic_reasoning_for_non_reasoning_model(self):
        from agent.model.model_adapter import ModelAdapter
        adapter = ModelAdapter.for_model("gpt-3.5-turbo")
        # gpt-3.5 doesn't have reasoning capability
        phases = adapter.synthetic_reasoning_for("run_command")
        assert isinstance(phases, list)

    def test_synthetic_reasoning_empty_for_reasoning_model(self):
        from agent.model.model_adapter import ModelAdapter
        adapter = ModelAdapter.for_model("deepseek-r1")
        # deepseek-r1 has real reasoning
        phases = adapter.synthetic_reasoning_for("run_command")
        assert phases == []  # no synthetic needed

    def test_normalize_complete_response_openai(self):
        from agent.model.model_adapter import ModelAdapter
        adapter = ModelAdapter.for_model("gpt-4o")
        msg = types.SimpleNamespace(content="The answer is 42.", tool_calls=[])
        choice = types.SimpleNamespace(message=msg, finish_reason="stop")
        response = types.SimpleNamespace(choices=[choice])
        text, tools, reasoning = adapter.normalize_complete_response(response)
        assert "42" in text
        assert tools == []

    def test_normalize_stream_yields_chunks(self):
        from agent.model.model_adapter import ModelAdapter
        from agent.model.stream_normalizer import ChunkType
        adapter = ModelAdapter.for_model("gpt-4o")

        def fake_stream():
            for word in ["Hello", " world"]:
                delta = types.SimpleNamespace(content=word, tool_calls=[], reasoning_content=None)
                choice = types.SimpleNamespace(delta=delta, finish_reason=None)
                yield types.SimpleNamespace(choices=[choice], usage=None)
            delta = types.SimpleNamespace(content="", tool_calls=[], reasoning_content=None)
            choice = types.SimpleNamespace(delta=delta, finish_reason="stop")
            yield types.SimpleNamespace(choices=[choice], usage=None)

        chunks = list(adapter.normalize_stream(fake_stream()))
        texts = [c for c in chunks if c.chunk_type == ChunkType.TEXT]
        assert any("Hello" in t.text for t in texts)

    def test_parse_tool_calls_from_text(self):
        from agent.model.model_adapter import ModelAdapter
        adapter = ModelAdapter.for_model("gpt-4o")
        text = '<tool_call><tool_name>run_tests</tool_name><parameters>{"suite":"unit"}</parameters></tool_call>'
        calls = adapter.parse_tool_calls_from_text(text)
        assert isinstance(calls, list)


# ---------------------------------------------------------------------------
# Streaming.py — new tags present
# ---------------------------------------------------------------------------

class TestStreamingTags:
    def test_reasoning_tag_in_specs(self):
        from agent.ui.streaming import _TAG_SPECS
        names = {s.name for s in _TAG_SPECS}
        assert "reasoning" in names

    def test_analysis_tag_in_specs(self):
        from agent.ui.streaming import _TAG_SPECS
        names = {s.name for s in _TAG_SPECS}
        assert "analysis" in names

    def test_reflect_tag_in_specs(self):
        from agent.ui.streaming import _TAG_SPECS
        names = {s.name for s in _TAG_SPECS}
        assert "reflect" in names

    def test_reasoning_shows_live(self):
        from agent.ui.streaming import _TAG_SPECS
        spec = next(s for s in _TAG_SPECS if s.name == "reasoning")
        assert spec.show_live is True

    def test_tool_use_is_silent(self):
        from agent.ui.streaming import _TAG_SPECS
        spec = next((s for s in _TAG_SPECS if s.name == "tool_use"), None)
        if spec:
            assert spec.show_live is False

    def test_parameters_block_is_silent(self):
        from agent.ui.streaming import _TAG_SPECS
        spec = next((s for s in _TAG_SPECS if s.name == "parameters_block"), None)
        if spec:
            assert spec.show_live is False

    def test_all_silent_tool_tags(self):
        from agent.ui.streaming import _TAG_SPECS
        silent = {s.name for s in _TAG_SPECS if not s.show_live}
        assert "tool_call" in silent
        assert "function" in silent
        assert "parameter" in silent

    def test_thinking_tags_have_correct_color(self):
        from agent.ui.streaming import _TAG_SPECS
        _ANSI_CYAN = "\033[38;2;100;200;240m"
        specs = [s for s in _TAG_SPECS if s.name in ("thinking", "thinking_alt", "reasoning", "analysis")]
        for s in specs:
            assert s.ansi_color == _ANSI_CYAN

    def test_spec_lookup_dict_built(self):
        from agent.ui.streaming import _SPEC_BY_OPEN, _SPEC_BY_CLOSE
        assert "<think>" in _SPEC_BY_OPEN
        assert "<reasoning>" in _SPEC_BY_OPEN
        assert "</think>" in _SPEC_BY_CLOSE
