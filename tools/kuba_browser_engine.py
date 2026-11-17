"""KUBA engine adapter over the checked-in KUBA browser operator helpers."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tools.browser_observation import observation_script, semantic_locator_script, summarize_accessibility


class KUBABrowserEngine:
    """Preserve KUBA's async engine API while using KUBA browser operator directly."""

    def __init__(
        self,
        helpers: Any,
        ensure_daemon: Any,
        workspace: Path,
        timeout_seconds: float = 90.0,
        allowed_domains: list[str] | None = None,
    ) -> None:
        self.helpers = helpers
        self.ensure_daemon = ensure_daemon
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self.headless = False
        self.allowed_domains = [domain.lower().removeprefix("*.") for domain in (allowed_domains or [])]
        self.connected = False
        self._lock = asyncio.Lock()

    async def _call(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    async def _ensure_connected(self) -> None:
        if not self.connected:
            await self.connect()

    def _check_url(self, url: str) -> None:
        if not self.allowed_domains:
            return
        hostname = (urlparse(url).hostname or "").lower()
        if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in self.allowed_domains):
            raise ValueError(f"Navigation to {hostname or url!r} is outside the configured browser domain allowlist")

    def _validate_selector(self, selector: str) -> dict[str, Any]:
        state = self.helpers.js(
            f"(()=>{{const a=[...document.querySelectorAll({json.dumps(selector)})];"
            "if(!a.length)return {count:0};if(a.length!==1)return {count:a.length};"
            "const e=a[0],s=getComputedStyle(e),r=e.getBoundingClientRect();"
            "return {count:1,visible:!!(e.checkVisibility?e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}):r.width&&r.height&&s.display!=='none'&&s.visibility!=='hidden'),"
            "enabled:!e.disabled&&e.getAttribute('aria-disabled')!=='true',tag:e.tagName.toLowerCase(),"
            "name:(e.getAttribute('aria-label')||e.innerText||e.textContent||e.getAttribute('placeholder')||'').trim().replace(/\\s+/g,' ').slice(0,240)}})()"
        )
        if not isinstance(state, dict) or not state.get("count"):
            raise ValueError(f"Element not found: {selector}")
        if state["count"] != 1:
            raise ValueError(f"Ambiguous selector {selector!r}: matched {state['count']} elements")
        if not state.get("visible"):
            raise ValueError(f"Element is not visible: {selector}")
        if not state.get("enabled"):
            raise ValueError(f"Element is disabled: {selector}")
        return state

    async def connect(self, cdp_url: str | None = None) -> dict[str, Any]:
        env = {"BU_CDP_URL": cdp_url} if cdp_url else None
        await asyncio.to_thread(self.ensure_daemon, env=env)
        self.connected = True
        tabs = await self._call(self.helpers.list_tabs)
        try:
            current = await self._call(self.helpers.current_tab)
        except Exception:
            current = tabs[0] if tabs else {"url": "about:blank"}
        return {
            "connected": True,
            "browser_kind": "kuba-cdp",
            "tab_count": len(tabs),
            "current_tab": current,
        }

    async def disconnect(self) -> None:
        # KUBA browser operator owns a reusable daemon and the user's whole Chrome instance.
        self.connected = False

    async def status(self) -> dict[str, Any]:
        if not self.connected:
            return {"connected": False, "browser_kind": "kuba-cdp", "tab_count": 0}
        tabs = await self._call(self.helpers.list_tabs)
        try:
            current = await self._call(self.helpers.current_tab)
        except Exception:
            current = None
        return {
            "connected": True,
            "browser_kind": "kuba-cdp",
            "tab_count": len(tabs),
            "current_tab": current,
        }

    async def navigate(self, url: str, wait_for_load: bool = True) -> dict[str, Any]:
        self._check_url(url)
        await self._ensure_connected()
        result = await self._call(self.helpers.goto_url, url)
        ready = False
        if wait_for_load:
            ready = await self._call(
                self.helpers.wait_for_load,
                timeout=min(self.timeout_seconds, 15.0),
            )
        try:
            page = await self._call(self.helpers.page_info)
        except Exception:
            page = {}
        return {
            "url": page.get("url", url) if isinstance(page, dict) else url,
            "requested_url": url,
            "title": page.get("title", "") if isinstance(page, dict) else "",
            "document_ready": ready,
            "navigation": result,
        }

    async def observe(self, query: str | None = None, mode: str = "compact") -> dict[str, Any]:
        await self._ensure_connected()
        page = await self._call(self.helpers.js, observation_script(query, mode))
        if not isinstance(page, dict):
            raise RuntimeError("KUBA could not read the current page")
        accessibility = await self._call(self.helpers.cdp, "Accessibility.getFullAXTree")
        nodes = accessibility.get("nodes", []) if isinstance(accessibility, dict) else []
        page["accessibility"] = summarize_accessibility(nodes, query, limit=80 if mode == "detailed" else 30)
        page["accessibilityTruncated"] = len(nodes) > len(page["accessibility"])
        return page

    async def new_tab(self, url: str | None = None) -> dict[str, Any]:
        target_url = url or "about:blank"
        if target_url != "about:blank":
            self._check_url(target_url)
        await self._ensure_connected()
        target_id = await self._call(self.helpers.new_tab, target_url)
        # KRYTH's configured browser preference is visible interaction.
        await self._call(self.helpers.activate_tab, target_id)
        return {"targetId": target_id, "url": target_url, "visible": True}

    async def list_tabs(self) -> list[dict[str, Any]]:
        await self._ensure_connected()
        return await self._call(self.helpers.list_tabs)

    async def switch_tab(self, target: str | None = None) -> dict[str, Any]:
        await self._ensure_connected()
        if target is None:
            current = await self._call(self.helpers.current_tab)
            target = current.get("targetId") or current.get("target_id")
        session_id = await self._call(self.helpers.switch_tab, target, activate=True)
        return {"targetId": target, "sessionId": session_id, "visible": True}

    async def close_tab(self, target: str | None = None) -> dict[str, Any]:
        await self._ensure_connected()
        await self._call(self.helpers.close_tab, target)
        return {"targetId": target}

    def _click(self, selector: str | None, x: int | None, y: int | None, button: str, clicks: int) -> dict[str, Any]:
        target_href = None
        if selector:
            self._validate_selector(selector)
            target_href = self.helpers.js(
                f"(()=>{{const e=document.querySelector({json.dumps(selector)});return e?.href||null}})()"
            )
            document = self.helpers.cdp("DOM.getDocument", depth=-1)
            node_id = self.helpers.cdp(
                "DOM.querySelector", nodeId=document["root"]["nodeId"], selector=selector
            ).get("nodeId")
            if not node_id:
                raise ValueError(f"Element not found: {selector}")
            model = self.helpers.cdp("DOM.getBoxModel", nodeId=node_id).get("model", {})
            points = model.get("content", [])
            if len(points) < 8:
                raise ValueError(f"Element has no visible box: {selector}")
            x = sum(points[0::2]) / 4
            y = sum(points[1::2]) / 4
        if x is None or y is None:
            raise ValueError("click requires a selector or x/y coordinates")
        self.helpers.click_at_xy(x, y, button=button, clicks=clicks)
        return {"selector": selector, "x": x, "y": y, "target_href": target_href}

    async def _page_signature(self) -> dict[str, Any]:
        script = """(()=>{const text=(document.body?.innerText||'').slice(0,8000);let hash=2166136261;for(let i=0;i<text.length;i++)hash=Math.imul(hash^text.charCodeAt(i),16777619);return {url:location.href,title:document.title,text_length:text.length,text_hash:(hash>>>0).toString(16),interactive_count:document.querySelectorAll('button,a[href],input,[role=button]').length}})()"""
        state = await self._call(self.helpers.js, script)
        return state if isinstance(state, dict) else {}

    def _resolve_semantic_selector(self, **criteria: str | None) -> str:
        result = self.helpers.js(semantic_locator_script(**criteria))
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
        await self._ensure_connected()
        criteria = {"role": role, "name": name, "label": label, "placeholder": placeholder, "text": text, "test_id": test_id}
        if any(criteria.values()):
            if selector or x is not None or y is not None:
                raise ValueError("Use either a semantic locator, selector, or coordinates")
            selector = await self._call(self._resolve_semantic_selector, **criteria)
        before = await self._call(self.helpers.page_info)
        before_url = before.get("url") if isinstance(before, dict) else None
        before_state = await self._page_signature()
        before_tabs = await self._call(self.helpers.list_tabs)
        result = await self._call(self._click, selector, x, y, button, clicks)
        target_href = result.get("target_href")
        before_ids = {tab.get("targetId") or tab.get("target_id") for tab in before_tabs}
        deadline = asyncio.get_running_loop().time() + 2.0
        after_state = await self._page_signature()
        after_tabs = await self._call(self.helpers.list_tabs)
        new_tabs = [tab for tab in after_tabs if (tab.get("targetId") or tab.get("target_id")) not in before_ids]
        target_tab = next((tab for tab in after_tabs if tab.get("url") == target_href), None) if target_href else None
        state_changed = after_state != before_state
        while not state_changed and not new_tabs and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.1)
            after_state = await self._page_signature()
            after_tabs = await self._call(self.helpers.list_tabs)
            new_tabs = [tab for tab in after_tabs if (tab.get("targetId") or tab.get("target_id")) not in before_ids]
            target_tab = next((tab for tab in after_tabs if tab.get("url") == target_href), None) if target_href else None
            state_changed = after_state != before_state

        after = await self._call(self.helpers.page_info)
        after_url = after.get("url") if isinstance(after, dict) else after_state.get("url")
        tab_opened = bool(new_tabs or (
            target_tab and (target_tab.get("targetId") or target_tab.get("target_id")) not in before_ids
        ))
        result.update({
            "before_url": before_url,
            "after_url": after_url,
            "state_changed": state_changed,
            "tab_opened": tab_opened,
        })

        # Some SPAs ignore CDP mouse events on link wrappers. Only fall back to
        # the link observed on the target element, after confirming no state change.
        if (
            isinstance(target_href, str)
            and target_href
            and target_href != before_url
            and not state_changed
            and not tab_opened
        ):
            navigation = await self.navigate(target_href)
            result["navigation_fallback"] = True
            result["verified_url"] = navigation.get("url")
        else:
            result["navigation_fallback"] = False
            result["verified_url"] = after_url if state_changed or tab_opened else None
        return result

    def _type_text(self, text: str, selector: str | None, press_enter: bool) -> dict[str, Any]:
        if selector:
            self._validate_selector(selector)
            focused = self.helpers.js(
                f"(()=>{{const el=document.querySelector({json.dumps(selector)});"
                "if(!el)return false;el.focus();return true;})()"
            )
            if not focused:
                raise ValueError(f"Element not found: {selector}")
        self.helpers.type_text(text)
        if press_enter:
            self.helpers.press_key("Enter")
        return {"text": text, "selector": selector}

    async def type_text(self, text: str, selector: str | None = None, press_enter: bool = False) -> dict[str, Any]:
        await self._ensure_connected()
        return await self._call(self._type_text, text, selector, press_enter)

    async def fill(self, selector: str, text: str, clear_first: bool = True) -> dict[str, Any]:
        await self._ensure_connected()
        self._validate_selector(selector)
        await self._call(self.helpers.fill_input, selector, text, clear_first=clear_first)
        return {"selector": selector, "text": text}

    async def press(self, key: str, modifiers: int = 0) -> dict[str, Any]:
        await self._ensure_connected()
        await self._call(self.helpers.press_key, key, modifiers=modifiers)
        return {"key": key, "modifiers": modifiers}

    async def scroll(self, x: int, y: int, dy: int = -300, dx: int = 0) -> dict[str, Any]:
        await self._ensure_connected()
        await self._call(self.helpers.scroll, x, y, dy=dy, dx=dx)
        return {"x": x, "y": y, "dy": dy, "dx": dx}

    def _hover(self, selector: str) -> dict[str, Any]:
        self._validate_selector(selector)
        expression = (
            f"(()=>{{const el=document.querySelector({json.dumps(selector)});"
            "if(!el)return null;const r=el.getBoundingClientRect();"
            "return {x:r.left+r.width/2,y:r.top+r.height/2};})()"
        )
        point = self.helpers.js(expression)
        if not point:
            raise ValueError(f"Element not found: {selector}")
        self.helpers.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=point["x"], y=point["y"])
        return {"selector": selector}

    async def hover(self, selector: str) -> dict[str, Any]:
        await self._ensure_connected()
        return await self._call(self._hover, selector)

    def _select(self, selector: str, option: str) -> dict[str, Any]:
        self._validate_selector(selector)
        expression = (
            f"(()=>{{const el=document.querySelector({json.dumps(selector)});"
            "if(!el)return false;const opt=[...el.options].find(o=>o.value==="
            f"{json.dumps(option)}||o.text==={json.dumps(option)});"
            "if(!opt)return false;el.value=opt.value;"
            "el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));return true;})()"
        )
        if not self.helpers.js(expression):
            raise ValueError(f"Select or option not found: {selector} / {option}")
        return {"selector": selector, "option": option}

    async def select(self, selector: str, option: str) -> dict[str, Any]:
        await self._ensure_connected()
        return await self._call(self._select, selector, option)

    async def screenshot(self, path: str | None = None, full: bool = False, max_dim: int | None = None) -> dict[str, Any]:
        await self._ensure_connected()
        screenshot_path = await self._call(self.helpers.capture_screenshot, path=path, full=full, max_dim=max_dim)
        result: dict[str, Any] = {"path": screenshot_path, "full": full}
        try:
            from PIL import Image

            with Image.open(screenshot_path) as image:
                result["width"], result["height"] = image.size
        except Exception:
            pass
        return result

    def _inspect(self, selector: str) -> dict[str, Any]:
        expression = (
            f"(()=>{{const el=document.querySelector({json.dumps(selector)});"
            "if(!el)return null;return {tag:el.tagName, text:el.innerText||el.value||'', "
            "attributes:Object.fromEntries([...el.attributes].map(a=>[a.name,a.value])), "
            "visible:!!(el.offsetWidth||el.offsetHeight||el.getClientRects().length), "
            "disabled:!!el.disabled};})()"
        )
        result = self.helpers.js(expression)
        if result is None:
            raise ValueError(f"Element not found: {selector}")
        return {"selector": selector, **result}

    async def inspect(self, selector: str) -> dict[str, Any]:
        await self._ensure_connected()
        await self._call(self._validate_selector, selector)
        return await self._call(self._inspect, selector)

    def _accessibility_tree(self, filter: str | None = None) -> dict[str, Any]:
        nodes = self.helpers.cdp("Accessibility.getFullAXTree").get("nodes", [])
        if filter:
            term = filter.casefold()
            nodes = [
                node for node in nodes
                if term in str(node.get("name", {}).get("value", "")).casefold()
                or term in str(node.get("role", {}).get("value", "")).casefold()
            ]
        return {"nodes": nodes[:2_000], "filtered": bool(filter), "truncated": len(nodes) > 2_000}

    async def get_accessibility_tree(self, filter: str | None = None) -> dict[str, Any]:
        await self._ensure_connected()
        return await self._call(self._accessibility_tree, filter)

    async def execute_javascript(self, expression: str, target_id: str | None = None) -> dict[str, Any]:
        await self._ensure_connected()
        result = await self._call(self.helpers.js, expression, target_id=target_id)
        return {"result": result, "expression": expression}

    async def upload_file(self, selector: str, path: str) -> dict[str, Any]:
        await self._ensure_connected()
        await self._call(self._validate_selector, selector)
        filepath = Path(path).expanduser().resolve(strict=True)
        if not filepath.is_file():
            raise ValueError("Upload path must point to a file")
        await self._call(self.helpers.upload_file, selector, str(filepath))
        return {"selector": selector, "path": str(filepath)}

    async def download(self, url: str, path: str) -> dict[str, Any]:
        self._check_url(url)
        await self._ensure_connected()
        destination = Path(path).expanduser().resolve()
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        content = await self._call(self.helpers.http_get, url)
        await asyncio.to_thread(destination.write_text, content, encoding="utf-8")
        return {"url": url, "path": str(destination), "bytes": destination.stat().st_size, "mode": "http_get"}

    async def load_domain_skills(self, domain: str) -> dict[str, Any]:
        root = (self.workspace / "domain-skills").resolve()
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
            text = path.read_text(encoding="utf-8", errors="replace")[: min(6_000, remaining)]
            if text:
                skills.append({"name": path.relative_to(root).as_posix(), "content": text})
                remaining -= len(text)
            if remaining <= 0:
                break
        return {"domain": normalized, "skills": skills}
