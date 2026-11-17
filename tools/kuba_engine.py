"""KUBA — in-process CDP browser engine for KRYTH.

Owns a Chrome DevTools Protocol connection directly via ``cdp_use``.  No
separate daemon, no IPC socket, no cloud dependency.  KRYTH is a long-running
process so the engine lives in-process and keeps one persistent CDP session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from tools.browser_observation import observation_script, semantic_locator_script, summarize_accessibility

from cdp_use.client import CDPClient

logger = logging.getLogger(__name__)


class KUBAEngine:
    """In-process CDP browser engine.

    Parameters mirror ``BrowserPluginConfig`` fields so the engine can be
    constructed straight from KRYTH config.
    """

    def __init__(
        self,
        headless: bool = False,
        timeout_seconds: float = 90.0,
        allowed_domains: list[str] | None = None,
    ) -> None:
        self.headless = headless
        self.timeout_seconds = timeout_seconds
        self.allowed_domains = allowed_domains or []
        self._client: CDPClient | None = None
        self._session_id: str | None = None
        self.connected = False

    # ------------------------------------------------------------------ connect / disconnect

    async def connect(self, cdp_url: str | None = None) -> dict[str, Any]:
        """Discover Chrome WS URL and open a CDP session.

        Resolution order: explicit ``cdp_url`` → ``BU_CDP_WS`` / ``BU_CDP_URL``
        env var → local Chrome on ports 9222 / 9223 → error.
        """
        import os

        url = cdp_url or os.environ.get("BU_CDP_WS") or os.environ.get("BU_CDP_URL")
        if not url:
            url = self._discover_local_chrome()
        if not url:
            return {
                "connected": False,
                "error": "No Chrome CDP endpoint found. Start Chrome with "
                "--remote-debugging-port=9222 or set BU_CDP_WS.",
            }

        self._client = CDPClient(url)
        await self._client.connect()
        await self._client.enable_domains(
            ["Page", "DOM", "Runtime", "Network", "Target", "Accessibility"]
        )
        self._session_id = await self._client.create_session()
        self.connected = True

        tabs = await self.list_tabs()
        current = tabs[0] if tabs else {"targetId": "unknown", "url": "about:blank"}
        return {
            "connected": True,
            "browser_kind": "cdp",
            "tab_count": len(tabs),
            "current_tab": current,
        }

    async def disconnect(self) -> None:
        """Close the CDP connection and clear state."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.debug("KUBA CDP close raised", exc_info=True)
            self._client = None
        self._session_id = None
        self.connected = False

    async def status(self) -> dict[str, Any]:
        """Return current connection status."""
        if not self.connected:
            return {"connected": False, "browser_kind": "cdp", "tab_count": 0}
        tabs = await self.list_tabs()
        current = tabs[0] if tabs else {"targetId": "unknown"}
        return {
            "connected": self.connected,
            "browser_kind": "cdp",
            "tab_count": len(tabs),
            "current_tab": current,
        }

    # ------------------------------------------------------------------ local Chrome discovery

    def _discover_local_chrome(self) -> str | None:
        """Look for a local Chrome instance with remote debugging enabled."""
        import socket

        for port in (9222, 9223):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    return f"http://127.0.0.1:{port}"
            except OSError:
                continue
        return None

    # ------------------------------------------------------------------ CDP helpers

    async def _cdp(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a CDP command on the current session."""
        if self._client is None:
            raise RuntimeError("KUBA engine not connected")
        return await self._client.send(method, params or {})

    async def _evaluate(self, expression: str) -> Any:
        response = await self._cdp(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        result = response.get("result", {}) if isinstance(response, dict) else {}
        details = response.get("exceptionDetails") if isinstance(response, dict) else None
        if details:
            exception = details.get("exception", {})
            raise RuntimeError(exception.get("description") or details.get("text") or "Page evaluation failed")
        return result.get("value")

    async def _resolve_semantic_selector(self, **criteria: str | None) -> str:
        result = await self._evaluate(semantic_locator_script(**criteria))
        if not isinstance(result, dict):
            raise RuntimeError("KUBA could not resolve the semantic locator")
        if result.get("error"):
            candidates = result.get("candidates")
            detail = f"; candidates: {candidates}" if candidates else ""
            raise ValueError(f"{result['error']}{detail}")
        selector = result.get("selector")
        if not selector:
            raise ValueError("Semantic match did not have a usable selector")
        return selector

    # ------------------------------------------------------------------ navigation / tabs

    async def navigate(self, url: str, wait_for_load: bool = True) -> dict[str, Any]:
        await self._cdp("Page.enable")
        await self._cdp("Network.enable")
        await self._cdp("Page.navigate", {"url": url})
        ready = False
        if wait_for_load:
            deadline = asyncio.get_running_loop().time() + min(self.timeout_seconds, 15.0)
            while asyncio.get_running_loop().time() < deadline:
                if await self._evaluate("document.readyState") == "complete":
                    ready = True
                    break
                await asyncio.sleep(0.15)
        page = await self._evaluate(
            "JSON.stringify({url:location.href,title:document.title,readyState:document.readyState})"
        )
        if isinstance(page, str):
            try:
                page = json.loads(page)
            except ValueError:
                page = {}
        page = page if isinstance(page, dict) else {}
        return {
            "url": page.get("url", url),
            "requested_url": url,
            "title": page.get("title", ""),
            "document_ready": ready,
        }

    async def observe(self, query: str | None = None, mode: str = "compact") -> dict[str, Any]:
        page = await self._evaluate(observation_script(query, mode))
        if not isinstance(page, dict):
            raise RuntimeError("KUBA could not read the current page")
        accessibility = await self._cdp("Accessibility.getFullAXTree", {})
        nodes = accessibility.get("nodes", []) if isinstance(accessibility, dict) else []
        page["accessibility"] = summarize_accessibility(nodes, query, limit=80 if mode == "detailed" else 30)
        page["accessibilityTruncated"] = len(nodes) > len(page["accessibility"])
        return page

    async def new_tab(self, url: str | None = None) -> dict[str, Any]:
        result = await self._cdp(
            "Target.createTarget", {"url": url or "about:blank"}
        )
        target_id = result.get("targetId") if isinstance(result, dict) else None
        return {"targetId": target_id, "url": url or "about:blank"}

    async def list_tabs(self) -> list[dict[str, Any]]:
        result = await self._cdp("Target.getTargets", {})
        targets = result.get("targetInfos", []) if isinstance(result, dict) else []
        return [
            {
                "targetId": t.get("targetId"),
                "url": t.get("url", "about:blank"),
                "title": t.get("title", ""),
            }
            for t in targets
        ]

    async def switch_tab(self, target: str | None = None) -> dict[str, Any]:
        if target is None:
            tabs = await self.list_tabs()
            target = tabs[0]["targetId"] if tabs else None
        await self._cdp("Target.activateTarget", {"targetId": target})
        return {"targetId": target}

    async def close_tab(self, target: str | None = None) -> dict[str, Any]:
        if target is None:
            tabs = await self.list_tabs()
            target = tabs[0]["targetId"] if tabs else None
        await self._cdp("Target.closeTarget", {"targetId": target})
        return {"targetId": target}

    # ------------------------------------------------------------------ element actions

    async def click(
        self,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
        role: str | None = None,
        name: str | None = None,
        label: str | None = None,
        placeholder: str | None = None,
        text: str | None = None,
        test_id: str | None = None,
    ) -> dict[str, Any]:
        criteria = {"role": role, "name": name, "label": label, "placeholder": placeholder, "text": text, "test_id": test_id}
        if any(criteria.values()):
            if selector or x is not None or y is not None:
                raise ValueError("Use either a semantic locator, selector, or coordinates")
            selector = await self._resolve_semantic_selector(**criteria)
        node = await self._resolve_element(selector) if selector else None
        if node is not None:
            box = await self._cdp(
                "DOM.getBoxModel", {"nodeId": node.get("nodeId")}
            )
            points = box.get("model", {}).get("content", []) if isinstance(box, dict) else []
            if len(points) >= 8:
                x = sum(points[0::2]) / 4
                y = sum(points[1::2]) / 4
            else:
                raise ValueError(f"Element has no clickable box: {selector}")
        if x is None or y is None:
            raise ValueError("click requires a selector or x/y coordinates")
        await self._cdp(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "button": button,
                "clicks": clicks,
                "x": x,
                "y": y,
            },
        )
        await self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseReleased", "button": button, "clicks": clicks, "x": x, "y": y},
        )
        return {"selector": selector, "x": x, "y": y}

    async def type_text(
        self,
        text: str,
        selector: str | None = None,
        press_enter: bool = False,
    ) -> dict[str, Any]:
        if selector:
            await self._focus(selector)
        await self._cdp("Input.insertText", {"text": text})
        if press_enter:
            await self.press("Enter")
        return {"text": text, "selector": selector}

    async def fill(self, selector: str, text: str, clear_first: bool = True) -> dict[str, Any]:
        node = await self._resolve_element(selector)
        await self._cdp("DOM.focus", {"nodeId": node.get("nodeId")})
        if clear_first:
            await self.press("CTRL+A", modifiers=2)
            await self.press("Backspace")
        await self._cdp("Input.insertText", {"text": text})
        return {"selector": selector, "text": text}

    async def press(self, key: str, modifiers: int = 0) -> dict[str, Any]:
        key_value = "Control" if key.upper() in {"CTRL", "CTRL+A", "CONTROL"} else key
        await self._cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": key_value, "modifiers": modifiers})
        if key.upper() == "CTRL+A":
            await self._cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": modifiers})
            await self._cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "a", "code": "KeyA", "modifiers": modifiers})
        await self._cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": key_value, "modifiers": modifiers})
        return {"key": key, "modifiers": modifiers}

    async def scroll(self, x: int, y: int, dy: int = -300, dx: int = 0) -> dict[str, Any]:
        await self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y},
        )
        await self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseWheel", "x": x, "y": y, "deltaX": dx, "deltaY": dy},
        )
        return {"x": x, "y": y}

    async def hover(self, selector: str) -> dict[str, Any]:
        node = await self._resolve_element(selector)
        box = await self._cdp("DOM.getBoxModel", {"nodeId": node.get("nodeId")})
        cx = box.get("content", [0, 0])[0]
        cy = box.get("content", [0, 0])[1]
        await self._cdp(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": cx, "y": cy},
        )
        return {"selector": selector}

    async def select(self, selector: str, option: str) -> dict[str, Any]:
        await self._resolve_element(selector)
        await self._cdp(
            "Runtime.evaluate",
            {
                "expression": (
                    f"(()=>{{const e=document.querySelector({json.dumps(selector)});"
                    f"const o=[...e.options].find(x=>x.value==={json.dumps(option)}||x.text==={json.dumps(option)});"
                    "if(!o)throw new Error('Option not found');e.value=o.value;"
                    "e.dispatchEvent(new Event('input',{bubbles:true}));"
                    "e.dispatchEvent(new Event('change',{bubbles:true}));return true})()"
                )
            },
        )
        return {"selector": selector, "option": option}

    # ------------------------------------------------------------------ page inspection

    async def screenshot(
        self,
        path: str | None = None,
        full: bool = False,
        max_dim: int | None = None,
    ) -> dict[str, Any]:
        result = await self._cdp(
            "Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": full},
        )
        data = result.get("data", "") if isinstance(result, dict) else ""
        if path:
            import base64

            with open(path, "wb") as fh:
                fh.write(base64.b64decode(data))
        return {"path": path or "screenshot.png", "full": full, "bytes": len(data)}

    async def inspect(self, selector: str) -> dict[str, Any]:
        node = await self._resolve_element(selector)
        attrs = await self._cdp("DOM.getAttributes", {"nodeId": node.get("nodeId")})
        visible = await self._cdp(
            "DOM.isVisible", {"nodeId": node.get("nodeId")}
        )
        return {
            "nodeId": node.get("nodeId"),
            "attributes": attrs or {},
            "visible": visible,
            "disabled": node.get("disabled", False),
            "selector": selector,
        }

    async def get_accessibility_tree(
        self, filter: str | None = None
    ) -> dict[str, Any]:
        result = await self._cdp("Accessibility.getFullAXTree", {})
        nodes = result.get("nodes", []) if isinstance(result, dict) else []
        if filter:
            nodes = [
                n
                for n in nodes
                if filter.lower()
                in (n.get("name", "") + n.get("role", "")).lower()
            ]
        return {"nodes": nodes, "filtered": bool(filter)}

    async def execute_javascript(
        self, expression: str, target_id: str | None = None
    ) -> dict[str, Any]:
        result = await self._cdp("Runtime.evaluate", {"expression": expression})
        return {"result": result, "expression": expression}

    # ------------------------------------------------------------------ file operations

    async def upload_file(self, selector: str, path: str) -> dict[str, Any]:
        node = await self._resolve_element(selector)
        await self._cdp(
            "DOM.setFileInputFiles",
            {"nodeId": node.get("nodeId"), "files": [path]},
        )
        return {"selector": selector, "path": path}

    async def download(self, url: str, path: str) -> dict[str, Any]:
        await self._cdp("Page.download", {"url": url})
        return {"url": url, "path": path, "bytes": 0}

    async def load_domain_skills(self, domain: str) -> dict[str, Any]:
        root = (Path(__file__).resolve().parents[1] / "browser" / "agent-workspace" / "domain-skills").resolve()
        normalized = domain.strip().lower().removeprefix("www.").strip(".")
        if not normalized or not re.fullmatch(r"[a-z0-9.-]+", normalized):
            raise ValueError("Domain must be a hostname or skill folder name")
        labels = normalized.split(".")
        candidates = [normalized, labels[0], "-".join(labels[-2:]), "-".join(reversed(labels[-2:])), *labels]
        files: list[Path] = []
        seen: set[Path] = set()
        for candidate in dict.fromkeys(candidates):
            folder = (root / candidate).resolve()
            if folder in seen or not folder.is_relative_to(root) or not folder.is_dir():
                continue
            seen.add(folder)
            files.extend(sorted(folder.glob("*.md")))
        skills = []
        remaining = 16_000
        for path in files[:10]:
            if not path.resolve().is_relative_to(root):
                continue
            content = path.read_text(encoding="utf-8", errors="replace")[: min(6_000, remaining)]
            if content:
                skills.append({"name": path.relative_to(root).as_posix(), "content": content})
                remaining -= len(content)
            if remaining <= 0:
                break
        return {"domain": normalized, "skills": skills}

    # ------------------------------------------------------------------ element resolution

    async def _resolve_element(self, selector: str) -> dict[str, Any]:
        """Resolve exactly one visible, enabled CSS target to a DOM node."""
        state = await self._evaluate(
            f"(()=>{{const a=[...document.querySelectorAll({json.dumps(selector)})];"
            "if(!a.length)return {count:0};if(a.length!==1)return {count:a.length};"
            "const e=a[0],s=getComputedStyle(e),r=e.getBoundingClientRect();"
            "return {count:1,visible:!!(e.checkVisibility?e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}):r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'),"
            "enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true'}})()"
        )
        if not isinstance(state, dict) or not state.get("count"):
            raise ValueError(f"Element not found: {selector}")
        if state["count"] != 1:
            raise ValueError(f"Ambiguous selector {selector!r}: matched {state['count']} elements")
        if not state.get("visible"):
            raise ValueError(f"Element is not visible: {selector}")
        if not state.get("enabled"):
            raise ValueError(f"Element is disabled: {selector}")
        document = await self._cdp("DOM.getDocument", {"depth": -1})
        root_id = document.get("root", {}).get("nodeId") if isinstance(document, dict) else None
        if root_id is None:
            raise RuntimeError("Browser did not return a document root")
        result = await self._cdp("DOM.querySelector", {"nodeId": root_id, "selector": selector})
        node = result.get("nodeId") if isinstance(result, dict) else None
        if node is None:
            raise ValueError(f"Element not found: {selector}")
        return {"nodeId": node, "selector": selector}

    async def _focus(self, selector: str) -> None:
        node = await self._resolve_element(selector)
        await self._cdp("DOM.focus", {"nodeId": node.get("nodeId")})
