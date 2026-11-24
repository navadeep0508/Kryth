from __future__ import annotations

import asyncio

from agent.events import AgentEvent
from client.llm_client import LLMClient
from client.response import StreamEvent, StreamEventType, TokenUsage
from config.config import Config, MCPServerConfig
from config.providers import ProviderConfig
from context.manager import ContextManager
from context.project_context import build_project_context
from ui.components.usage import usage_line
from ui.tui import TUI
from rich.console import Console
from utils.text import estimate_serialized_request


def test_provider_context_window_overrides_model_default():
    config = Config()
    config.providers.add(
        ProviderConfig(name="local", base_url="http://localhost/v1", context_window=4096)
    )
    assert config.effective_context_window == 4096


def test_provider_tokenizer_is_selected_when_configured():
    config = Config()
    config.providers.add(
        ProviderConfig(
            name="hosted", base_url="https://api.example/v1",
            context_window=8192, tokenizer_encoding="cl100k_base",
        )
    )
    estimate = estimate_serialized_request(
        {"messages": [{"role": "user", "content": "Inspect src/main.py"}]},
        model=config.model_name,
        tokenizer_encoding=config.effective_tokenizer_encoding,
    )
    assert estimate.estimator == "tiktoken:cl100k_base"
    assert estimate.estimated_tokens > 0


def test_unknown_model_uses_conservative_serialized_byte_estimate():
    estimate = estimate_serialized_request(
        {"messages": [{"role": "user", "content": "é" * 200}], "tools": [{"name": "read"}]},
        model="private-unknown-model",
    )
    assert estimate.estimator == "utf8-byte-upper-bound"
    assert estimate.estimated_tokens >= estimate.serialized_bytes


def test_usage_event_keeps_request_and_turn_counters_separate():
    request = TokenUsage(prompt_tokens=120, completion_tokens=20, total_tokens=140)
    turn = TokenUsage(prompt_tokens=900, completion_tokens=100, total_tokens=1000)
    event = AgentEvent.usage(request, turn)
    assert event.data["usage"]["prompt_tokens"] == 120
    assert event.data["turn_usage"]["prompt_tokens"] == 900


def test_263101_provider_tokens_report_overflow_without_invalid_percentage():
    line = usage_line(
        {"prompt_tokens": 263_101, "completion_tokens": 675},
        256_000,
        {"prompt_tokens": 263_101, "total_tokens": 263_776},
    )
    assert "overflow" in line.plain.casefold()
    assert "263,101" in line.plain
    assert "263,776" in line.plain
    assert "102.8%" not in line.plain


def test_unavailable_or_invalid_provider_usage_is_not_reused_as_current_request():
    config = Config()
    tui = TUI(config, Console(record=True))
    tui.update_turn_usage({"prompt_tokens": 120}, {"total_tokens": 140})
    tui.update_turn_usage({"prompt_tokens": "stale"}, {"total_tokens": 160})

    assert tui.context_state == "unavailable"
    assert tui.context_tokens is None
    assert tui.turn_tokens == 160


def test_status_line_reports_effective_window_and_separate_turn_usage():
    config = Config()
    config.model.context_window = 256_000
    tui = TUI(config, Console(record=True))
    tui.update_turn_usage(
        {"prompt_tokens": 263_101},
        {"prompt_tokens": 263_776, "total_tokens": 263_776},
    )
    repl = __import__("ui.repl.app", fromlist=["Repl"])
    app = repl.Repl.__new__(repl.Repl)
    app.tui = tui
    app.config = config
    app.console = Console(width=180)
    app._bottom_fragments = repl.Repl._bottom_fragments.__get__(app)
    rendered = "".join(part[1] for part in app._bottom_fragments())

    assert "overflow" in rendered.casefold()
    assert "256,000" in rendered
    assert "263,776" in rendered
    assert "102.8%" not in rendered


def test_usage_resets_clear_request_and_turn_measurements():
    tui = TUI(Config(), Console(record=True))
    tui.update_turn_usage({"prompt_tokens": 120}, {"total_tokens": 140})
    tui.reset_turn_summary()
    tui.reset_usage()
    assert tui.context_state == "unavailable"
    assert tui.turn_tokens is None


def test_oversized_request_is_rejected_before_client_creation(monkeypatch):
    config = Config()
    config.model.context_window = 1024
    config.model.max_output_tokens = 128
    client = LLMClient(config)
    monkeypatch.setattr(client, "get_client", lambda: (_ for _ in ()).throw(AssertionError("network client created")))

    async def collect():
        return [event async for event in client.chat_completion([{"role": "user", "content": "x" * 2000}])]

    events = asyncio.run(collect())
    assert len(events) == 1
    assert events[0].type is StreamEventType.ERROR
    assert "blocked before sending" in events[0].error
    assert client.context_budget_history[-1]["status"] == "rejected"
    assert events[0].context_budget == client.context_budget_history[-1]


def test_request_preflight_accounts_for_options_tools_and_output_reserve(monkeypatch):
    config = Config()
    config.model.context_window = 2048
    config.model.max_output_tokens = 5000
    config.model.name = "gpt-4o"
    client = LLMClient(config)
    captured = {}

    async def fake_stream(_client, kwargs):
        captured.update(kwargs)
        yield StreamEvent(type=StreamEventType.MESSAGE_COMPLETE)

    monkeypatch.setattr(client, "get_client", lambda: object())
    monkeypatch.setattr(client, "_stream_response", fake_stream)

    async def collect():
        return [event async for event in client.chat_completion(
            [{"role": "user", "content": "short"}],
            tools=[{"name": "read", "parameters": {"type": "object"}}],
        )]

    events = asyncio.run(collect())
    budget = client.context_budget_history[-1]
    assert events[0].type is StreamEventType.CONTEXT_BUDGET
    assert budget["status"] == "admitted"
    assert budget["reserved_output_tokens"] == 512
    assert captured["max_tokens"] == 512
    assert "tools" in captured and "tool_choice" in captured
    assert budget["estimator"].startswith("tiktoken:")
    assert budget["estimated_input_tokens"] <= budget["input_budget"]


def test_large_tool_result_is_saved_and_truncated(monkeypatch, tmp_path):
    import context.manager as manager_module

    monkeypatch.setattr(manager_module, "get_data_dir", lambda: tmp_path)
    config = Config()
    context = ContextManager(config, user_memory=None)
    full_output = "tool output line\n" * 12_000

    context.add_tool_result("test-call", full_output)
    message = context._messages[-1]
    assert len(message.content) < len(full_output)
    assert "Full output saved at:" in message.content
    output_path = message.content.rsplit("Full output saved at: ", 1)[1]
    assert (tmp_path / "tool-outputs").is_dir()
    from pathlib import Path

    assert Path(output_path).read_text(encoding="utf-8") == full_output


def test_pruning_keeps_full_output_reference(monkeypatch, tmp_path):
    import context.manager as manager_module

    monkeypatch.setattr(manager_module, "get_data_dir", lambda: tmp_path)
    context = ContextManager(Config(), user_memory=None)
    context.add_user_message("first request")
    context.add_tool_result("test-call", "long output line\n" * 12_000)
    context.PRUNE_PROTECT_TOKENS = 10
    context.PRUNE_MINIMUM_TOKENS = 5
    context.add_user_message("second request")

    assert context.prune_tool_outputs() == 1
    assert "Full output remains at:" in context._messages[1].content


def test_compression_threshold_triggers_during_long_tool_loops(monkeypatch, tmp_path):
    import context.manager as manager_module

    monkeypatch.setattr(manager_module, "get_data_dir", lambda: tmp_path)
    config = Config()
    config.model.context_window = 4096
    context = ContextManager(config, user_memory=None)
    context.add_user_message("Run the long validation task")
    for index in range(14):
        context.add_tool_result(f"call-{index}", "diagnostic line\n" * 5000)
    assert context.needs_compression()


def test_workspace_detection_uses_only_active_project_markers(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "civiceye"\n', encoding="utf-8")
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "agent.py").write_text("# other project's agent", encoding="utf-8")
    context = build_project_context(Config(cwd=tmp_path), tmp_path)
    assert context.active_project == tmp_path.name
    assert context.active_project_root == tmp_path
    assert context.is_kryth_source is False
    assert context.kryth_runtime_root != tmp_path


def test_workspace_detection_identifies_kryth_source_only_inside_workspace(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "kryth"\n', encoding="utf-8")
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "agent.py").write_text("# KRYTH agent", encoding="utf-8")
    context = build_project_context(Config(cwd=tmp_path), tmp_path)
    assert context.is_kryth_source is True


def test_workspace_lists_external_integrations_from_config_only(tmp_path):
    config = Config(cwd=tmp_path, mcp_servers={"docs": MCPServerConfig(command="docs-mcp")})
    config.providers.add(ProviderConfig(name="hosted", base_url="https://api.example/v1"))
    context = build_project_context(config, tmp_path)
    assert context.external_integrations == ["mcp:docs", "provider:hosted"]

