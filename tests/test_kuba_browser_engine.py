from __future__ import annotations

import asyncio
from pathlib import Path

from tools.kuba_browser_engine import KUBABrowserEngine


class _Helpers:
    def __init__(self):
        self.calls = []

    def list_tabs(self):
        self.calls.append(("list_tabs", (), {}))
        return [{"targetId": "tab-1", "url": "https://example.com", "title": "Example"}]

    def current_tab(self):
        self.calls.append(("current_tab", (), {}))
        return {"targetId": "tab-1", "url": "https://example.com", "title": "Example"}

    def new_tab(self, url):
        self.calls.append(("new_tab", (url,), {}))
        return "tab-new"

    def activate_tab(self, target):
        self.calls.append(("activate_tab", (target,), {}))

    def goto_url(self, url):
        self.calls.append(("goto_url", (url,), {}))
        return {"frameId": "frame-1"}

    def wait_for_load(self, timeout):
        self.calls.append(("wait_for_load", (), {"timeout": timeout}))
        return True

    def page_info(self):
        self.calls.append(("page_info", (), {}))
        return {"url": "https://example.com/final", "title": "Example", "readyState": "complete"}

    def js(self, expression):
        self.calls.append(("js", (expression,), {}))
        return {
            "url": "https://example.com",
            "title": "Example",
            "readyState": "complete",
            "visibleText": "Example page",
            "interactiveElements": [],
            "formFields": [],
            "dialogs": [],
            "alerts": [],
            "loadingIndicators": [],
        }

    def cdp(self, method, **params):
        self.calls.append(("cdp", (method,), params))
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": 7}
        if method == "DOM.getBoxModel":
            return {"model": {"content": [1, 2, 5, 2, 5, 7, 1, 7]}}
        return {"nodes": [
            {"role": {"value": "heading"}, "name": {"value": "Example"}},
            {"role": {"value": "textbox"}, "name": {"value": "Private field"},
             "value": {"value": "must not leak"}},
        ]}

    def click_at_xy(self, x, y, button="left", clicks=1):
        self.calls.append(("click_at_xy", (x, y), {"button": button, "clicks": clicks}))


def test_connect_reuses_the_folder_daemon_and_reports_browser_tabs():
    helpers = _Helpers()
    daemon_calls = []

    def ensure_daemon(**kwargs):
        daemon_calls.append(kwargs)

    engine = KUBABrowserEngine(helpers, ensure_daemon, workspace=Path("."))
    result = asyncio.run(engine.connect())

    assert result["connected"] is True
    assert result["browser_kind"] == "kuba-cdp"
    assert result["tab_count"] == 1
    assert daemon_calls == [{"env": None}]


def test_new_tab_is_activated_to_keep_browser_visible():
    helpers = _Helpers()
    engine = KUBABrowserEngine(
        helpers,
        lambda **_kwargs: None,
        workspace=Path("."),
    )

    result = asyncio.run(engine.new_tab("https://example.com"))

    assert result["visible"] is True
    assert ("new_tab", ("https://example.com",), {}) in helpers.calls
    assert ("activate_tab", ("tab-new",), {}) in helpers.calls


def test_navigation_reports_final_url_and_document_readiness():
    helpers = _Helpers()
    engine = KUBABrowserEngine(helpers, lambda **_kwargs: None, workspace=Path("."))

    result = asyncio.run(engine.navigate("https://example.com/start"))

    assert result["requested_url"] == "https://example.com/start"
    assert result["url"] == "https://example.com/final"
    assert result["title"] == "Example"
    assert result["document_ready"] is True


def test_observe_returns_bounded_dom_summary_and_redacted_accessibility():
    helpers = _Helpers()
    engine = KUBABrowserEngine(helpers, lambda **_kwargs: None, workspace=Path("."))

    result = asyncio.run(engine.observe("Example", mode="compact"))

    assert result["visibleText"] == "Example page"
    assert result["accessibility"] == [
        {"index": 0, "role": "heading", "name": "Example", "focusable": False},
        {"index": 1, "role": "textbox", "name": "Private field", "focusable": False},
    ]
    assert "must not leak" not in str(result)
    script = next(args[0] for method, args, _kwargs in helpers.calls if method == "js")
    assert '"Example"' in script


def test_selector_validation_rejects_ambiguous_elements_before_clicking():
    class AmbiguousHelpers(_Helpers):
        def js(self, expression):
            self.calls.append(("js", (expression,), {}))
            return {"count": 2}

    helpers = AmbiguousHelpers()
    engine = KUBABrowserEngine(helpers, lambda **_kwargs: None, workspace=Path("."))

    try:
        asyncio.run(engine.click(selector="button"))
    except ValueError as exc:
        assert "Ambiguous selector" in str(exc)
    else:
        raise AssertionError("Expected an ambiguous target to be rejected")
    assert not any(call[0] == "click_at_xy" for call in helpers.calls)


def test_semantic_click_resolves_role_and_name_to_unique_observed_target():
    class SemanticHelpers(_Helpers):
        def js(self, expression):
            self.calls.append(("js", (expression,), {}))
            if "const q=" in expression:
                return {"count": 1, "selector": "a:nth-of-type(2)"}
            if "querySelectorAll" in expression:
                return {"count": 1, "visible": True, "enabled": True}
            return {}

    helpers = SemanticHelpers()
    engine = KUBABrowserEngine(helpers, lambda **_kwargs: None, workspace=Path("."))

    result = asyncio.run(engine.click(role="link", name="Python tutorial"))

    assert result["selector"] == "a:nth-of-type(2)"
    assert any(call[0] == "click_at_xy" for call in helpers.calls)


def test_link_click_navigates_to_observed_href_when_click_does_not_change_page():
    class LinkHelpers(_Helpers):
        def js(self, expression):
            self.calls.append(("js", (expression,), {}))
            if "e?.href" in expression:
                return "https://example.com/video"
            return {"count": 1, "visible": True, "enabled": True}

    helpers = LinkHelpers()
    engine = KUBABrowserEngine(helpers, lambda **_kwargs: None, workspace=Path("."))
    result = asyncio.run(engine.click(selector="a.video"))

    assert result["navigation_fallback"] is True
    assert result["verified_url"] == "https://example.com/final"
    assert ("goto_url", ("https://example.com/video",), {}) in helpers.calls


def test_click_waits_for_delayed_dom_update_before_verifying():
    class DelayedHelpers(_Helpers):
        def __init__(self):
            super().__init__()
            self.signatures = 0

        def js(self, expression):
            self.calls.append(("js", (expression,), {}))
            if "interactive_count" in expression:
                self.signatures += 1
                return {
                    "url": "https://example.com",
                    "title": "Example",
                    "text_hash": "ready" if self.signatures >= 4 else "initial",
                }
            if "e?.href" in expression:
                return None
            return {"count": 1, "visible": True, "enabled": True}

    helpers = DelayedHelpers()
    engine = KUBABrowserEngine(helpers, lambda **_kwargs: None, workspace=Path("."))
    result = asyncio.run(engine.click(selector="button.load"))
    assert result["state_changed"] is True
    assert result["navigation_fallback"] is False


def test_click_detects_popup_tab_without_repeating_action():
    class PopupHelpers(_Helpers):
        def __init__(self):
            super().__init__()
            self.tabs = [{"targetId": "tab-1", "url": "https://example.com", "title": "Example"}]

        def js(self, expression):
            self.calls.append(("js", (expression,), {}))
            if "interactive_count" in expression:
                return {"url": "https://example.com", "title": "Example", "text_hash": "same"}
            if "e?.href" in expression:
                return "https://example.com/popup"
            return {"count": 1, "visible": True, "enabled": True}

        def list_tabs(self):
            self.calls.append(("list_tabs", (), {}))
            return list(self.tabs)

        def click_at_xy(self, x, y, button="left", clicks=1):
            super().click_at_xy(x, y, button=button, clicks=clicks)
            self.tabs.append({"targetId": "popup-1", "url": "https://example.com/popup"})

    helpers = PopupHelpers()
    engine = KUBABrowserEngine(helpers, lambda **_kwargs: None, workspace=Path("."))
    result = asyncio.run(engine.click(selector="a.popup"))
    assert result["tab_opened"] is True
    assert result["navigation_fallback"] is False
    assert len([call for call in helpers.calls if call[0] == "click_at_xy"]) == 1


def test_domain_allowlist_blocks_navigation_before_browser_call():
    helpers = _Helpers()
    engine = KUBABrowserEngine(
        helpers,
        lambda **_kwargs: None,
        workspace=Path("."),
        allowed_domains=["example.com"],
    )

    try:
        asyncio.run(engine.navigate("https://not-example.com"))
    except ValueError as exc:
        assert "outside the configured" in str(exc)
    else:
        raise AssertionError("Expected the configured domain allowlist to reject navigation")
    assert helpers.calls == []


def test_domain_skill_lookup_reads_only_matching_local_skill_files(tmp_path):
    skill_dir = tmp_path / "domain-skills" / "youtube"
    skill_dir.mkdir(parents=True)
    (skill_dir / "playback.md").write_text("Use the visible player controls.", encoding="utf-8")
    engine = KUBABrowserEngine(_Helpers(), lambda **_kwargs: None, workspace=tmp_path)

    result = asyncio.run(engine.load_domain_skills("www.youtube.com"))

    assert result["domain"] == "youtube.com"
    assert result["skills"] == [
        {"name": "youtube/playback.md", "content": "Use the visible player controls."}
    ]
