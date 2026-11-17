from __future__ import annotations

import asyncio
import os
from typing import Any

from config.config import ApprovalPolicy, Config
from hooks.hook_system import HookSystem
from tools.base import ExecutionStatus, ToolInvocation
from tools.browser_plugin import BrowserConnectTool, BrowserPluginManager, BrowserPluginTool
from tools.browser_plugin import _configure_browser_runtime_output, _ACTION_PARAM_MODELS
from prompts.tool_guidelines import get_tool_guidelines_section
from tools.registry import ToolRegistry
from safety.approval import ApprovalManager


class _FakeKubaEngine:
    """Fake KUBAEngine that records calls and returns canned results."""

    def __init__(self, headless: bool = False, timeout_seconds: float = 90.0,
                 allowed_domains: list[str] | None = None):
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self.allowed_domains = allowed_domains or []
        self.calls: list[dict[str, Any]] = []
        self.connected = False
        self.tabs = [{"targetId": "tab-1", "url": "about:blank", "title": "New Tab"}]
        self.active_tab = "tab-1"
        self.page_title = "Example"
        self.visible_text = "Example page"

    async def connect(self, cdp_url: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "connect", "cdp_url": cdp_url})
        self.connected = True
        return {"connected": True, "browser_kind": "cdp", "tab_count": 1,
                "current_tab": {"targetId": "tab-1", "url": "about:blank", "title": "New Tab"}}

    async def disconnect(self) -> None:
        self.calls.append({"method": "disconnect"})
        self.connected = False

    async def status(self) -> dict[str, Any]:
        self.calls.append({"method": "status"})
        return {"connected": self.connected, "browser_kind": "cdp",
                "tab_count": len(self.tabs), "current_tab": {"targetId": self.active_tab}}

    async def navigate(self, url: str, wait_for_load: bool = True) -> dict[str, Any]:
        self.calls.append({"method": "navigate", "url": url, "wait_for_load": wait_for_load})
        self.tabs = [dict(self.tabs[0], url=url, title=self.page_title)]
        return {"url": url, "requested_url": url, "title": self.page_title, "document_ready": True}

    async def click(self, selector: str | None = None, x: int | None = None,
                    y: int | None = None, button: str = "left",
                    clicks: int = 1) -> dict[str, Any]:
        self.calls.append({"method": "click", "selector": selector, "x": x,
                           "y": y, "button": button, "clicks": clicks})
        return {"selector": selector, "x": x, "y": y}

    async def type_text(self, text: str, selector: str | None = None,
                        press_enter: bool = False) -> dict[str, Any]:
        self.calls.append({"method": "type_text", "text": text, "selector": selector,
                           "press_enter": press_enter})
        return {"text": text, "selector": selector}

    async def fill(self, selector: str, text: str, clear_first: bool = True) -> dict[str, Any]:
        self.calls.append({"method": "fill", "selector": selector, "text": text,
                           "clear_first": clear_first})
        return {"selector": selector, "text": text}

    async def press(self, key: str, modifiers: int = 0) -> dict[str, Any]:
        self.calls.append({"method": "press", "key": key, "modifiers": modifiers})
        return {"key": key, "modifiers": modifiers}

    async def scroll(self, x: int, y: int, dy: int = -300, dx: int = 0) -> dict[str, Any]:
        self.calls.append({"method": "scroll", "x": x, "y": y, "dy": dy, "dx": dx})
        return {"x": x, "y": y}

    async def hover(self, selector: str) -> dict[str, Any]:
        self.calls.append({"method": "hover", "selector": selector})
        return {"selector": selector}

    async def select(self, selector: str, option: str) -> dict[str, Any]:
        self.calls.append({"method": "select", "selector": selector, "option": option})
        return {"selector": selector, "option": option}

    async def screenshot(self, path: str | None = None, full: bool = False,
                         max_dim: int | None = None) -> dict[str, Any]:
        self.calls.append({"method": "screenshot", "path": path, "full": full,
                           "max_dim": max_dim})
        return {"path": path or "screenshot.png", "full": full, "bytes": 1234}

    async def inspect(self, selector: str) -> dict[str, Any]:
        self.calls.append({"method": "inspect", "selector": selector})
        return {"nodeId": 1, "attributes": {}, "visible": True, "disabled": False,
                "selector": selector, "text": getattr(self, "input_value", "")}

    async def get_accessibility_tree(self, filter: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "get_accessibility_tree", "filter": filter})
        return {
            "nodes": [{"role": {"value": "heading"}, "name": {"value": "Python select video"}}],
            "filtered": bool(filter),
        }

    async def observe(self, query: str | None = None, mode: str = "compact") -> dict[str, Any]:
        self.calls.append({"method": "observe", "query": query, "mode": mode})
        return {
            "url": self.tabs[-1].get("url", "https://example.com"),
            "title": self.page_title,
            "visibleText": self.visible_text,
            "interactiveElements": [],
        }

    async def execute_javascript(self, expression: str,
                                 target_id: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "execute_javascript", "expression": expression,
                           "target_id": target_id})
        return {"result": 42, "expression": expression}

    async def upload_file(self, selector: str, path: str) -> dict[str, Any]:
        self.calls.append({"method": "upload_file", "selector": selector, "path": path})
        return {"selector": selector, "path": path}

    async def download(self, url: str, path: str) -> dict[str, Any]:
        self.calls.append({"method": "download", "url": url, "path": path})
        return {"url": url, "path": path, "bytes": 100}

    async def new_tab(self, url: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "new_tab", "url": url})
        tab = {"targetId": "tab-new", "url": url or "about:blank", "title": "New Tab"}
        self.tabs.append(tab)
        self.active_tab = "tab-new"
        return {"targetId": "tab-new", "url": tab["url"]}

    async def list_tabs(self) -> list[dict[str, Any]]:
        self.calls.append({"method": "list_tabs"})
        return list(self.tabs)

    async def switch_tab(self, target: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "switch_tab", "target": target})
        self.active_tab = target or "tab-1"
        return {"targetId": target or "tab-1"}

    async def close_tab(self, target: str | None = None) -> dict[str, Any]:
        self.calls.append({"method": "close_tab", "target": target})
        self.tabs = [tab for tab in self.tabs if tab["targetId"] != (target or self.active_tab)]
        return {"targetId": target or "tab-1"}

    async def load_domain_skills(self, domain: str) -> dict[str, Any]:
        self.calls.append({"method": "load_domain_skills", "domain": domain})
        return {"domain": domain, "skills": []}


def _fake_kuba_engine() -> _FakeKubaEngine:
    return _FakeKubaEngine()


# ------------------------------------------------------------------ tests

def test_browser_tool_discovers_kuba_actions():
    engine = _fake_kuba_engine()
    tool = BrowserPluginTool(Config(), engine)

    expected = {
        "navigate", "click", "type", "fill", "press", "scroll", "hover", "select",
        "screenshot", "inspect", "get_accessibility_tree", "execute_javascript",
        "observe", "upload_file", "download", "new_tab", "list_tabs", "switch_tab", "close_tab",
        "domain_skills", "done",
    }
    assert set(_ACTION_PARAM_MODELS) == expected
    schema = tool.to_openai_schema()
    properties = schema["parameters"]["properties"]
    assert schema["parameters"]["type"] == "object"
    assert set(properties["action"]["enum"]) == expected
    assert {"role", "name", "label", "placeholder", "text", "test_id"}.issubset(
        properties.keys()
    )
    assert "expected" in properties
    assert "navigate(url*" in schema["description"]

    flat_call = {"action": "navigate", "url": "https://example.com"}
    assert tool.validate_params(flat_call) == []
    assert flat_call == {"action": "navigate", "params": {"url": "https://example.com"}}
    assert tool.validate_params({"action": "navigate", "params": {"url": "https://example.com"}}) == []
    assert tool.validate_params({"action": "done", "params": {}}) == []


def test_browser_runtime_telemetry_is_disabled(monkeypatch):
    monkeypatch.setenv("ANONYMIZED_TELEMETRY", "true")

    _configure_browser_runtime_output()

    assert os.environ["ANONYMIZED_TELEMETRY"] == "false"


def test_browser_window_is_visible_by_default():
    assert Config().browser_plugin.headless is False


def test_browser_actions_reuse_one_session_and_return_results(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        first = await tool.execute(ToolInvocation(
            params={"action": "navigate", "params": {"url": "https://example.com"}},
            cwd=tmp_path,
        ))
        assert first.execution_status is ExecutionStatus.STATE_VERIFIED
        second = await tool.execute(ToolInvocation(
            params={"action": "inspect", "params": {"selector": "h1"}},
            cwd=tmp_path,
        ))
        assert first.success and "navigate" in first.output
        assert second.success and "inspect" in second.output
        assert '"visible":true' in second.output
        tree = await tool.execute(ToolInvocation(
            params={"action": "get_accessibility_tree", "params": {}},
            cwd=tmp_path,
        ))
        assert tree.success and "Python select video" in tree.output
        assert engine.connected
        # connect + navigate + verification observe + inspect + accessibility tree
        assert len(engine.calls) == 5
        assert engine.calls[0]["method"] == "connect"
        assert engine.calls[1]["method"] == "navigate"
        assert engine.calls[2]["method"] == "observe"
        assert engine.calls[3]["method"] == "inspect"
        assert engine.calls[4]["method"] == "get_accessibility_tree"
        await tool.close()
        assert not engine.connected

    asyncio.run(run())


def test_observe_action_is_read_only_and_preserves_page_context(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        invocation = ToolInvocation(
            params={"action": "observe", "params": {"query": "results", "mode": "compact"}},
            cwd=tmp_path,
        )

        confirmation = await tool.get_confirmation(invocation)
        result = await tool.execute(invocation)

        assert confirmation is None
        assert result.success
        assert "Example page" in result.output
        assert '"title":"Example"' in result.output
        assert engine.calls[-1] == {"method": "observe", "query": "results", "mode": "compact"}

    asyncio.run(run())


def test_browser_search_expected_outcome_requires_submitted_query_and_results(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        engine.page_title = "Python tutorial results"
        engine.visible_text = "Python tutorial results " + ("Intro to Python programming. " * 8)
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={
                "action": "navigate",
                "params": {
                    "url": "https://www.youtube.com/results?search_query=python+tutorial",
                    "expected": {"kind": "browser_search", "query": "python tutorial"},
                },
            },
            cwd=tmp_path,
        ))

        assert result.success
        assert result.expected_state_verified
        assert not result.user_objective_completed
        assert result.verification["expected_outcome"]["evidence"]["query_submitted"]

    asyncio.run(run())


def test_browser_search_expectation_fails_when_only_search_home_is_observed(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        engine.visible_text = "Search Python tutorials here"
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={
                "action": "navigate",
                "params": {
                    "url": "https://www.youtube.com/",
                    "expected": {"kind": "browser_search", "query": "python tutorial"},
                },
            },
            cwd=tmp_path,
        ))

        assert not result.success
        assert result.expected_state_verified is False
        assert result.execution_status is ExecutionStatus.VERIFICATION_FAILED

    asyncio.run(run())


def test_browser_actions_are_serialized_per_shared_session(tmp_path):
    class ConcurrentEngine(_FakeKubaEngine):
        def __init__(self):
            super().__init__()
            self.active_navigations = 0
            self.max_active_navigations = 0

        async def navigate(self, url: str, wait_for_load: bool = True) -> dict[str, Any]:
            self.active_navigations += 1
            self.max_active_navigations = max(self.max_active_navigations, self.active_navigations)
            await asyncio.sleep(0.01)
            self.active_navigations -= 1
            return await super().navigate(url, wait_for_load)

    async def run():
        engine = ConcurrentEngine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)

        results = await asyncio.gather(*[
            tool.execute(ToolInvocation(
                params={"action": "navigate", "params": {"url": f"https://example.com/{index}"}},
                cwd=tmp_path,
            ))
            for index in range(3)
        ])

        assert all(result.success for result in results)
        assert engine.max_active_navigations == 1

    asyncio.run(run())


def test_flat_provider_arguments_are_normalized_before_registry_execution(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        registry = ToolRegistry(Config(cwd=tmp_path))
        registry.register(tool)
        raw_params = {"action": "navigate", "url": "https://example.com"}

        result = await registry.invoke(
            "browser", raw_params, tmp_path, HookSystem(Config(cwd=tmp_path))
        )

        assert result.success
        assert raw_params == {"action": "navigate", "params": {"url": "https://example.com"}}
        # connect then navigate
        assert engine.calls[0]["method"] == "connect"
        assert engine.calls[1]["method"] == "navigate"
        assert engine.calls[1]["url"] == "https://example.com"
        await tool.close()

    asyncio.run(run())


def test_sensitive_browser_actions_use_kryth_approval_and_reads_do_not():
    engine = _fake_kuba_engine()
    tool = BrowserPluginTool(Config(), engine)
    assert not tool.is_mutating({"action": "inspect"})
    assert not tool.is_mutating({"action": "list_tabs"})
    assert tool.is_mutating({"action": "click"})
    assert tool.is_mutating({"action": "navigate"})
    confirmation = asyncio.run(tool.get_confirmation(ToolInvocation(
        params={"action": "click", "params": {"selector": "button"}},
        cwd=Config().cwd,
    )))
    assert confirmation is not None
    assert confirmation.is_dangerous
    assert "click" in confirmation.description


def test_readonly_browser_actions_skip_confirmation():
    engine = _fake_kuba_engine()
    tool = BrowserPluginTool(Config(), engine)
    confirmation = asyncio.run(tool.get_confirmation(ToolInvocation(
        params={"action": "list_tabs", "params": {}},
        cwd=Config().cwd,
    )))
    assert confirmation is None


def test_domain_skills_load_without_connecting_to_browser(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={"action": "domain_skills", "params": {"domain": "shopify.com"}},
            cwd=tmp_path,
        ))
        assert result.success
        assert "shopify.com" in result.output
        assert engine.calls == [{"method": "load_domain_skills", "domain": "shopify.com"}]

    asyncio.run(run())


def test_rejected_browser_mutation_never_reaches_kuba(tmp_path):
    async def run():
        config = Config(cwd=tmp_path, approval=ApprovalPolicy.ON_REQUEST)
        engine = _fake_kuba_engine()
        tool = BrowserPluginTool(config, engine)
        registry = ToolRegistry(config)
        registry.register(tool)

        async def reject(_confirmation):
            return False

        approval = ApprovalManager(config.approval, tmp_path, reject)
        result = await registry.invoke(
            "browser",
            {"action": "click", "params": {"selector": "button"}},
            tmp_path,
            HookSystem(config),
            approval,
        )
        assert not result.success
        assert result.error == "User rejected the operation"
        assert engine.calls == []

    asyncio.run(run())


def test_browser_plugin_config_can_disable_discovery():
    manager = BrowserPluginManager(Config(browser_plugin={"enabled": False}))
    assert manager.discover() == []
    assert manager.engine is None

    restricted = BrowserPluginManager(Config(allowed_tools=["read"]))
    assert restricted.discover() == []


def test_session_registers_discovered_browser_plugins(tmp_path):
    from agent.session import Session

    session = Session(Config(cwd=tmp_path, browser_plugin={"enabled": True}))
    # Replace discover with a stub that returns tools
    stub_browser = BrowserPluginTool(Config(), _fake_kuba_engine())
    session.browser_plugin_manager.discover = lambda: [stub_browser]

    asyncio.run(session.initalize())

    assert session.tool_registry.get("browser") is stub_browser


def test_browser_action_failure_is_returned_as_tool_error(tmp_path):
    async def run():
        engine = _fake_kuba_engine()

        async def fail(**kwargs):
            raise RuntimeError("browser disconnected")

        engine.navigate = fail
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={"action": "navigate", "params": {"url": "https://example.com"}},
            cwd=tmp_path,
        ))
        assert not result.success
        assert "browser disconnected" in (result.error or "")
        await tool.close()

    asyncio.run(run())


def test_navigation_without_ready_page_is_verification_failure(tmp_path):
    class StalledEngine(_FakeKubaEngine):
        async def navigate(self, url, wait_for_load=True):
            self.calls.append({"method": "navigate", "url": url})
            return {"requested_url": url, "url": url, "title": "", "document_ready": False}

    async def run():
        engine = StalledEngine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={"action": "navigate", "params": {"url": "https://example.com"}},
            cwd=tmp_path,
        ))
        assert not result.success
        assert result.execution_status is ExecutionStatus.VERIFICATION_FAILED
        assert result.verification["document_ready"] is False

    asyncio.run(run())


def test_unexpected_navigation_redirect_is_not_verified(tmp_path):
    class RedirectEngine(_FakeKubaEngine):
        async def navigate(self, url, wait_for_load=True):
            self.calls.append({"method": "navigate", "url": url})
            self.tabs[0].update(url="https://unexpected.example/login", title="Login")
            self.page_title = "Login"
            return {
                "requested_url": url, "url": "https://unexpected.example/login",
                "title": "Login", "document_ready": True,
            }

    async def run():
        engine = RedirectEngine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={"action": "navigate", "params": {"url": "https://example.com"}},
            cwd=tmp_path,
        ))
        assert not result.success
        assert result.execution_status is ExecutionStatus.VERIFICATION_FAILED
        assert result.verification["unexpected_redirect"] is True

    asyncio.run(run())


def test_click_without_observable_change_is_not_reported_as_verified(tmp_path):
    async def run():
        engine = _FakeKubaEngine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={"action": "click", "params": {"selector": "button"}},
            cwd=tmp_path,
        ))
        assert not result.success
        assert result.execution_status is ExecutionStatus.VERIFICATION_FAILED
        assert "no navigation" in result.error

    asyncio.run(run())


def test_click_popup_is_verified_against_the_live_tab_list(tmp_path):
    class PopupEngine(_FakeKubaEngine):
        async def click(self, **kwargs):
            self.calls.append({"method": "click", **kwargs})
            self.tabs.append({
                "targetId": "popup-1", "url": "https://example.com/popup", "title": "Popup"
            })
            return {
                "tab_opened": True,
                "target_href": "https://example.com/popup",
                "opened_tab_ids": ["popup-1"],
            }

    async def run():
        engine = PopupEngine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={"action": "click", "params": {"selector": "a.popup"}},
            cwd=tmp_path,
        ))
        assert result.success
        assert result.execution_status is ExecutionStatus.STATE_VERIFIED
        assert result.verification["opened_tab_verified"] is True

    asyncio.run(run())


def test_disconnected_engine_reconnects_before_action(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        tool._connected = True
        engine.connected = False
        result = await tool.execute(ToolInvocation(
            params={"action": "observe", "params": {}}, cwd=tmp_path
        ))
        assert result.success
        assert sum(call["method"] == "connect" for call in engine.calls) == 1
        assert result.execution_status is ExecutionStatus.STATE_VERIFIED

    asyncio.run(run())


def test_same_browser_call_id_is_deduplicated(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        invocation = ToolInvocation(
            params={"action": "navigate", "params": {"url": "https://example.com"}},
            cwd=tmp_path,
            call_id="tool-call-1",
        )
        first = await tool.execute(invocation)
        second = await tool.execute(invocation)
        assert first is second
        assert sum(call["method"] == "navigate" for call in engine.calls) == 1

    asyncio.run(run())


def test_browser_action_surfaces_failed_connection_without_calling_action(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        engine.connect = async_connect_failed
        tool = BrowserPluginTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(
            params={"action": "navigate", "params": {"url": "https://youtube.com"}},
            cwd=tmp_path,
        ))

        assert not result.success
        assert "No Chrome CDP endpoint found" in (result.error or "")
        assert engine.calls == []

    async def async_connect_failed(*_args, **_kwargs):
        return {
            "connected": False,
            "error": "No Chrome CDP endpoint found. Start Chrome with --remote-debugging-port=9222.",
        }

    asyncio.run(run())


def test_browser_connect_reports_engine_connection_failure(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        engine.connect = async_connect_failed
        tool = BrowserConnectTool(Config(cwd=tmp_path), engine)
        result = await tool.execute(ToolInvocation(params={}, cwd=tmp_path))

        assert not result.success
        assert "No Chrome CDP endpoint found" in (result.error or "")
        assert not tool._connected

    async def async_connect_failed(*_args, **_kwargs):
        return {
            "connected": False,
            "error": "No Chrome CDP endpoint found. Start Chrome with --remote-debugging-port=9222.",
        }

    asyncio.run(run())


def test_browser_connect_disconnect_status_tools(tmp_path):
    async def run():
        engine = _fake_kuba_engine()
        # Create session tools directly
        from tools.browser_plugin import BrowserConnectTool, BrowserDisconnectTool, BrowserStatusTool
        connect = BrowserConnectTool(Config(cwd=tmp_path), engine)
        disconnect = BrowserDisconnectTool(Config(cwd=tmp_path), engine)
        status = BrowserStatusTool(Config(cwd=tmp_path), engine)

        result = await connect.execute(ToolInvocation(params={}, cwd=tmp_path))
        assert result.success
        assert engine.connected

        result = await status.execute(ToolInvocation(params={}, cwd=tmp_path))
        assert result.success
        assert "connected=True" in result.output or "connected" in result.output.lower()

        result = await disconnect.execute(ToolInvocation(params={}, cwd=tmp_path))
        assert result.success
        assert not engine.connected

        result = await connect.execute(ToolInvocation(params={}, cwd=tmp_path))
        assert result.success
        assert engine.connected
        assert [call["method"] for call in engine.calls].count("connect") == 2

    asyncio.run(run())


def test_upload_and_download_are_mutating():
    engine = _fake_kuba_engine()
    tool = BrowserPluginTool(Config(), engine)
    assert tool.is_mutating({"action": "upload_file", "params": {"selector": "input", "path": "/tmp/f.txt"}})
    assert tool.is_mutating({"action": "download", "params": {"url": "https://example.com/file", "path": "/tmp/f.txt"}})


def test_browser_guidelines_avoid_stale_youtube_selectors_and_require_verification():
    browser = BrowserPluginTool(Config(), _fake_kuba_engine())

    guidelines = get_tool_guidelines_section([browser])

    assert "youtube.com/results?search_query=" in guidelines
    assert "Do not guess selectors" in guidelines
    assert "confirm the search results match the query" in guidelines
