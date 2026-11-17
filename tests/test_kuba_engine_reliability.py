from __future__ import annotations

import asyncio
import json

from tools.kuba_engine import KUBAEngine


class _CDPClient:
    def __init__(self, *, selector_count=1):
        self.calls = []
        self.selector_count = selector_count

    async def send(self, method, params):
        self.calls.append((method, params))
        if method == "Runtime.evaluate":
            expression = params["expression"]
            if "interactiveElements" in expression:
                value = {
                    "url": "https://example.test/results",
                    "title": "Results",
                    "readyState": "complete",
                    "visibleText": "Python tutorial",
                    "interactiveElements": [],
                    "formFields": [],
                    "dialogs": [],
                    "alerts": [],
                    "loadingIndicators": [],
                }
            elif "const q=" in expression:
                value = {"count": 1, "selector": "a:nth-of-type(2)"}
            elif "querySelectorAll" in expression:
                value = {"count": self.selector_count, "visible": True, "enabled": True}
            elif expression == "document.readyState":
                value = "complete"
            else:
                value = json.dumps({"url": "https://example.test/results", "title": "Results"})
            return {"result": {"value": value}}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": [{"role": "heading", "name": "Python tutorial"}]}
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": 12}
        if method == "DOM.getBoxModel":
            return {"model": {"content": [1, 2, 5, 2, 5, 7, 1, 7]}}
        return {}


def _engine(client):
    engine = KUBAEngine()
    engine._client = client
    engine.connected = True
    return engine


def test_fallback_observe_returns_dom_and_accessibility_context():
    engine = _engine(_CDPClient())

    result = asyncio.run(engine.observe("Python"))

    assert result["visibleText"] == "Python tutorial"
    assert result["accessibility"][0]["name"] == "Python tutorial"


def test_fallback_rejects_ambiguous_css_before_resolving_node():
    client = _CDPClient(selector_count=3)
    engine = _engine(client)

    try:
        asyncio.run(engine._resolve_element("button"))
    except ValueError as exc:
        assert "Ambiguous selector" in str(exc)
    else:
        raise AssertionError("Expected an ambiguous selector to fail")
    assert not any(method == "DOM.querySelector" for method, _params in client.calls)


def test_fallback_navigation_waits_for_dom_and_reports_final_page_identity():
    engine = _engine(_CDPClient())

    result = asyncio.run(engine.navigate("https://example.test/results"))

    assert result == {
        "url": "https://example.test/results",
        "requested_url": "https://example.test/results",
        "title": "Results",
        "document_ready": True,
    }


def test_fallback_click_uses_visible_box_center_and_releases_mouse():
    client = _CDPClient()
    engine = _engine(client)

    result = asyncio.run(engine.click(selector="#open-video"))

    assert result["x"] == 3
    assert result["y"] == 4.5
    mouse_events = [params for method, params in client.calls if method == "Input.dispatchMouseEvent"]
    assert [event["type"] for event in mouse_events] == ["mousePressed", "mouseReleased"]


def test_fallback_fill_uses_input_events_instead_of_node_id_zero():
    client = _CDPClient()
    engine = _engine(client)

    asyncio.run(engine.fill("#search", "python"))

    assert any(method == "Input.insertText" and params == {"text": "python"} for method, params in client.calls)
    assert not any(method == "DOM.setNodeValue" for method, _params in client.calls)


def test_fallback_semantic_click_resolves_role_and_accessible_name():
    client = _CDPClient()
    engine = _engine(client)

    result = asyncio.run(engine.click(role="link", name="Python tutorial"))

    assert result["selector"] == "a:nth-of-type(2)"
    assert any(method == "DOM.querySelector" for method, _params in client.calls)
