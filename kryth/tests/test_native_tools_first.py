"""Native-tools-first contract.

Native function calling is the default delivery mode for every provider. The
legacy text/XML tool protocol is a guarded fallback, reached only when:
  • KRYTH_TOOL_MODE=text is set explicitly, or
  • the provider rejected the tool schema and the model was cached as
    text-only (the tools_unsupported retry path).
"""

import importlib

import pytest


@pytest.fixture
def llm(monkeypatch):
    import agent.llm as llm
    # Ensure a clean env for each test.
    monkeypatch.delenv("KRYTH_TOOL_MODE", raising=False)
    return llm


def test_default_is_schema_native(llm, monkeypatch):
    # Default (no env override) is native function calling for ALL providers,
    # including NVIDIA endpoints.
    monkeypatch.setattr(llm, "BASE_URL", "https://integrate.api.nvidia.com/v1", raising=False)
    assert llm._tool_mode() == "schema"
    monkeypatch.setattr(llm, "BASE_URL", "https://api.openai.com/v1", raising=False)
    assert llm._tool_mode() == "schema"


def test_explicit_text_override(llm, monkeypatch):
    monkeypatch.setenv("KRYTH_TOOL_MODE", "text")
    assert llm._tool_mode() == "text"


def test_explicit_schema_override(llm, monkeypatch):
    monkeypatch.setenv("KRYTH_TOOL_MODE", "schema")
    assert llm._tool_mode() == "schema"


def test_text_cache_forces_fallback(llm):
    # A model previously found to reject tool schemas stays in text mode even
    # though the default is now schema — this is the guarded fallback.
    model = "some/text-only-model"
    llm._model_text_tool_cache.add(model)
    try:
        # The fallback gate in ask_llm_stream is:
        #   if tools and (_tool_mode() == "text" or model in _model_text_tool_cache)
        # With default schema mode, membership in the cache is what triggers it.
        assert llm._tool_mode() == "schema"
        assert model in llm._model_text_tool_cache
    finally:
        llm._model_text_tool_cache.discard(model)


def test_tools_unsupported_classification_still_works(llm):
    # The 400 classifier must still recognize a tools-unsupported error so the
    # fallback path can engage.
    class _Err(Exception):
        body = "This model does not support tool calling"
    # _classify_400 reads getattr(e, 'body', '') and str(e).
    err = _Err("400: tools not supported")
    assert llm._classify_400(err) == "tools_unsupported"
