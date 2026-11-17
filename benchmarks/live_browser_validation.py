"""Run a safe live browser workflow through KRYTH's checked-in KUBA adapter.

Run explicitly with ``python -m benchmarks.live_browser_validation``. The script
only searches, opens pages, reads visible listings, switches tabs, and checks
reconnection. It never purchases or submits forms beyond search.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

from config.config import ApprovalPolicy, Config
from benchmarks.execution_quality import BENCHMARK_CASES, BenchmarkSample, calculate_metrics
from hooks.hook_system import HookSystem
from safety.approval import ApprovalManager
from tools.browser_plugin import BrowserPluginManager
from tools.base import ExecutionStatus
from tools.registry import ToolRegistry
from utils.semantic_verification import SemanticVerifier


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "reports" / "KRYTH_AUTONOMOUS_EXECUTION_V3_LIVE_EVIDENCE.json"
GOOGLE_QUERY = "Python programming tutorial"
YOUTUBE_QUERY = "Python tutorial for beginners"
AMAZON_QUERY = "python programming books"


def compare_visible_listings(listings: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare only price/rating values actually exposed in visible cards."""
    priced = []
    rated = []
    for item in listings:
        title = str(item.get("title", ""))
        price = str(item.get("price", ""))
        rating = str(item.get("rating", ""))
        price_match = re.search(r"[\d,]+(?:\.\d{1,2})?", price)
        rating_match = re.search(r"(\d+(?:\.\d+)?)\s+out of 5", rating, re.I)
        if title and price_match:
            priced.append((float(price_match.group(0).replace(",", "")), item))
        if title and rating_match:
            rated.append((float(rating_match.group(1)), item))
    comparison: dict[str, Any] = {"visible_listing_count": len(listings)}
    if priced:
        value, item = min(priced, key=lambda entry: entry[0])
        comparison["lowest_visible_price"] = {
            "title": item.get("title"), "displayed_price": item.get("price"), "numeric_value": value,
        }
    if rated:
        value, item = max(rated, key=lambda entry: entry[0])
        comparison["highest_visible_rating"] = {
            "title": item.get("title"), "displayed_rating": item.get("rating"), "numeric_value": value,
        }
    comparison["limitations"] = "Only values visible in the current search results were compared; review counts and product-page details were not inferred."
    return comparison


class LiveWorkflow:
    def __init__(self) -> None:
        self.config = Config(cwd=ROOT, approval=ApprovalPolicy.ON_REQUEST)
        self.config.browser_plugin.allowed_domains = [
            "google.com", "youtube.com", "amazon.com", "docs.python.org",
            "realpython.com", "developer.mozilla.org", "geeksforgeeks.org",
            "w3schools.com", "programiz.com",
        ]
        self.manager = BrowserPluginManager(self.config)
        self.tools = self.manager.discover()
        if self.manager.engine is None or type(self.manager.engine).__name__ != "KUBABrowserEngine":
            raise RuntimeError("The checked-in KUBA browser adapter is unavailable; no fallback engine is used.")
        self.registry = ToolRegistry(self.config)
        for tool in self.tools:
            self.registry.register(tool)
        self.hooks = HookSystem(self.config)
        self.approvals: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []

        async def authorize(confirmation):
            self.approvals.append({
                "tool": confirmation.tool_name,
                "description": confirmation.description,
                "risk_level": confirmation.risk_level,
            })
            return True

        self.approval = ApprovalManager(self.config.approval, ROOT, authorize)
        self.steps: dict[str, dict[str, Any]] = {}
        self.initial_tabs: list[dict[str, Any]] = []
        self.context_stats = {"requests": 0, "tokens": None}
        self.previous: dict[str, Any] = {}
        if EVIDENCE_PATH.is_file():
            try:
                prior = json.loads(EVIDENCE_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                prior = {}
            if prior.get("status") in {"in_progress", "partial_or_failed"} and prior.get("engine") == "KUBABrowserEngine":
                self.previous = _sanitize_data(prior)
                self.steps.update(self.previous.get("tool_steps") or {})

    async def call(self, tool_name: str, params: dict[str, Any] | None = None):
        safe_params = _sanitize_data(params or {})
        started = time.perf_counter()
        result = await self.registry.invoke(
            tool_name,
            params or {},
            ROOT,
            self.hooks,
            self.approval,
        )
        self.tool_calls.append({
            "tool": tool_name,
            "params": safe_params,
            "success": bool(result.success),
            "execution_status": result.execution_status.value if result.execution_status else None,
            "action_executed": result.action_executed,
            "state_changed": result.state_changed,
            "expected_state_verified": result.expected_state_verified,
            "duration_seconds": round(time.perf_counter() - started, 3),
        })
        return result

    def save_step(self, name: str, result, started: float, **evidence: Any) -> bool:
        self.steps[name] = {
            "executed": bool(result.action_executed),
            "success": bool(result.success),
            "execution_status": result.execution_status.value if result.execution_status else None,
            "action_executed": result.action_executed,
            "state_changed": result.state_changed,
            "expected_state_verified": result.expected_state_verified,
            "verification": result.verification,
            "duration_seconds": round(time.perf_counter() - started, 3),
            **evidence,
        }
        self.persist_progress(name)
        if "expected_outcome" in result.verification:
            return bool(result.success and result.expected_state_verified)
        return bool(
            result.success
            and result.execution_status not in {
                ExecutionStatus.ACTION_FAILED,
                ExecutionStatus.VERIFICATION_FAILED,
            }
        )

    def persist_progress(self, phase: str) -> None:
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = {
            **self.previous,
            "status": "in_progress",
            "engine": "KUBABrowserEngine",
            "phase": phase,
            "tool_steps": self.steps,
            "tool_calls": self.tool_calls,
            "approvals": self.approvals,
            "checkpointed_at": datetime.now(timezone.utc).isoformat(),
        }
        temporary = EVIDENCE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(EVIDENCE_PATH)

    async def find_tab(self, host: str, predicate=None) -> dict[str, Any] | None:
        tabs = await self.manager.engine.list_tabs()
        return next((
            tab for tab in tabs
            if host in (urlparse(str(tab.get("url", ""))).hostname or "").lower()
            and (predicate is None or predicate(tab))
        ), None)

    async def run_google(self) -> dict[str, Any]:
        tab = await self.find_tab("google.com", lambda item: "/search" in item.get("url", ""))
        if tab:
            await self.call("browser", {"action": "switch_tab", "target": tab.get("targetId") or tab.get("target_id")})
        else:
            await self.call("browser", {"action": "new_tab", "url": "https://www.google.com"})

        search_url = "https://www.google.com/search?q=" + quote_plus(GOOGLE_QUERY)
        started = time.perf_counter()
        result = await self.call("browser", {
            "action": "navigate",
            "url": search_url,
            "expected": {"kind": "browser_search", "query": GOOGLE_QUERY},
        })
        passed = self.save_step("google_search", result, started, query=GOOGLE_QUERY)
        page = await self.manager.engine.observe(mode="detailed")
        links = [
            element for element in page.get("interactiveElements", [])
            if element.get("tag") == "a"
            and str(element.get("href", "")).startswith("http")
            and "google.com" not in (urlparse(element.get("href", "")).hostname or "")
            and element.get("name")
        ]
        links.sort(key=lambda item: (
            "docs.python.org" not in item.get("href", ""),
            "tutorial" not in item.get("name", "").casefold(),
        ))
        destination: dict[str, Any] | None = None
        if links:
            choice = links[0]
            dest_host = urlparse(choice["href"]).hostname or ""
            if dest_host in {"docs.python.org", "realpython.com", "developer.mozilla.org", "geeksforgeeks.org", "w3schools.com", "programiz.com"}:
                started = time.perf_counter()
                click_result = await self.call("browser", {
                    "action": "click",
                    "selector": choice["selector"],
                    "expected": {
                        "kind": "browser_navigation",
                        "url_contains": dest_host,
                        "text_contains": "The Python Tutorial",
                    },
                })
                click_passed = self.save_step(
                    "google_result_open",
                    click_result,
                    started,
                    selected_title=choice["name"],
                    selected_url=choice["href"],
                )
                destination = await self.manager.engine.observe(mode="compact")
                passed = passed and click_passed
            else:
                self.steps["google_result_open"] = {
                    "executed": False,
                    "success": False,
                    "reason": f"Observed candidate domain is outside the live-test allowlist: {dest_host}",
                    "candidate_title": choice["name"],
                    "candidate_url": choice["href"],
                }
        else:
            self.steps["google_result_open"] = {
                "executed": False,
                "success": False,
                "reason": "Google observation did not expose a relevant external result link.",
            }
        return {"search_page": self._page_summary(page), "destination": self._page_summary(destination) if destination else None, "passed": passed}

    async def run_youtube(self) -> dict[str, Any]:
        tab = await self.find_tab("youtube.com")
        if tab:
            started = time.perf_counter()
            switch_result = await self.call("browser", {"action": "switch_tab", "target": tab.get("targetId") or tab.get("target_id")})
            self.save_step("youtube_tab", switch_result, started, tab={"id": tab.get("targetId") or tab.get("target_id"), "url": tab.get("url")}, reused=True)
        else:
            started = time.perf_counter()
            tab_result = await self.call("browser", {"action": "new_tab", "url": "https://www.youtube.com"})
            self.save_step("youtube_tab", tab_result, started, reused=False)

        current = await self.manager.engine.observe(mode="compact")
        current_query = parse_qs(urlparse(str(current.get("url", ""))).query).get("search_query", [""])[0]
        if "youtube.com/watch?v=" in str(current.get("url", "")):
            # Resume from an already open watch page. Re-observe both the
            # results state and the video state instead of clicking again.
            video_url = str(current["url"])
            video_page = await self.manager.engine.observe(mode="detailed")
            video_check = SemanticVerifier(ROOT).verify(
                {
                    "kind": "browser_video",
                    "url_contains": "youtube.com/watch?v=",
                    "title_contains": "Python",
                },
                video_page,
                action_executed=False,
                state_changed=False,
            )
            results_url = "https://www.youtube.com/results?search_query=" + quote_plus(YOUTUBE_QUERY)
            started = time.perf_counter()
            search_result = await self.call("browser", {
                "action": "navigate",
                "url": results_url,
            })
            self.save_step("youtube_search", search_result, started, query=YOUTUBE_QUERY, resumed=True)
            search_page = None
            search_check = None
            for _ in range(15):
                await asyncio.sleep(1)
                search_page = await self.manager.engine.observe(mode="detailed")
                search_check = SemanticVerifier(ROOT).verify(
                    {"kind": "browser_search", "query": YOUTUBE_QUERY},
                    search_page,
                    action_executed=True,
                    state_changed=str(search_page.get("url", "")) == results_url,
                )
                if search_check.expected_state_verified:
                    break
            search_passed = bool(search_check and search_check.expected_state_verified)
            self.steps["youtube_search"].update({
                "success": search_passed,
                "expected_state_verified": search_passed,
                "verification": search_check.to_dict() if search_check else None,
                "observation_poll_seconds": 15,
            })
            self.persist_progress("youtube_search")
            started = time.perf_counter()
            video_result = await self.call("browser", {
                "action": "navigate",
                "url": video_url,
                "expected": {
                    "kind": "browser_video",
                    "url_contains": "youtube.com/watch?v=",
                    "title_contains": "Python",
                },
            })
            video_passed = self.save_step("youtube_video", video_result, started, resumed_from_observed_video=True)
            final_video = await self.manager.engine.observe(mode="detailed")
            video_summary = self._page_summary(final_video)
            return {
                # The final navigation carries the fresh URL/title verification;
                # the pre-resume observation is diagnostic and may be sparse.
                "passed": bool(search_passed and video_passed),
                "search_page": self._page_summary(search_page),
                "video": video_summary,
            }
        already_searched = (
            current_query.casefold() == YOUTUBE_QUERY.casefold()
            and bool((self.steps.get("youtube_search") or {}).get("success"))
        )
        fields = current.get("formFields", [])
        search = next((field for field in fields if field.get("name") == "search_query"), None)
        if not search and not already_searched:
            self.steps["youtube_search"] = {
                "executed": False, "success": False,
                "reason": "No visible YouTube search field was observed.",
                "page": self._page_summary(current),
            }
            return {"passed": False, "search_page": self._page_summary(current), "video": None}

        fill_passed = True
        search_passed = already_searched
        if not already_searched:
            selector = next((
                item.get("selector") for item in current.get("interactiveElements", [])
                if item.get("tag") == "input" and item.get("name") == "Search"
            ), None)
            if not selector:
                selector = "input[name='search_query']"
            started = time.perf_counter()
            fill_result = await self.call("browser", {
                "action": "fill", "selector": selector, "text": YOUTUBE_QUERY,
            })
            fill_passed = self.save_step("youtube_fill", fill_result, started, selector=selector, query=YOUTUBE_QUERY)
            if not fill_passed:
                self.steps["youtube_search"] = {"executed": False, "success": False, "reason": "Search input value was not verified."}
                self.persist_progress("youtube_fill")
                return {"passed": False, "search_page": None, "video": None}

            started = time.perf_counter()
            search_result = await self.call("browser", {
                "action": "click",
                "role": "button",
                "name": "Search",
                "expected": {"kind": "browser_search", "query": YOUTUBE_QUERY},
            })
            search_passed = self.save_step("youtube_search", search_result, started, query=YOUTUBE_QUERY)
        else:
            semantic = SemanticVerifier(ROOT).verify(
                {"kind": "browser_search", "query": YOUTUBE_QUERY},
                current,
                action_executed=True,
                state_changed=False,
            )
            search_passed = semantic.expected_state_verified
            self.steps["youtube_search"] = {
                "executed": False,
                "success": search_passed,
                "expected_state_verified": search_passed,
                "verification": semantic.to_dict(),
                "reused_verified_search_state": True,
            }
            self.persist_progress("youtube_search")
        search_page = await self.manager.engine.observe(query=YOUTUBE_QUERY, mode="detailed")
        videos = [
            item for item in search_page.get("interactiveElements", [])
            if item.get("tag") == "a"
            and "youtube.com/watch?v=" in str(item.get("href", ""))
            and item.get("name")
        ]
        videos.sort(key=lambda item: (
            "python for beginners (full course)" not in item.get("name", "").casefold(),
            "python for beginners" not in item.get("name", "").casefold(),
            "python tutorial" not in item.get("name", "").casefold(),
        ))
        video_summary = None
        video_passed = False
        if videos:
            video = videos[0]
            started = time.perf_counter()
            open_result = await self.call("browser", {
                "action": "click",
                "selector": video["selector"],
                "expected": {
                    "kind": "browser_video",
                    "url_contains": "youtube.com/watch?v=",
                    # YouTube watch titles can use an episode title rather than
                    # the enclosing playlist/search-result title. Verify the
                    # shared topic in the page title and visible video text.
                    "title_contains": "Python",
                },
            })
            video_passed = self.save_step(
                "youtube_video",
                open_result,
                started,
                selected_title=video["name"],
                selected_url=video["href"],
            )
            observed = await self.manager.engine.observe(mode="compact")
            video_summary = self._page_summary(observed)
            if not video_passed:
                current_url = str(observed.get("url", ""))
                results_url = str(search_page.get("url", ""))
                if current_url == results_url and "youtube.com/results" in current_url:
                    self.steps["youtube_video_recovery"] = {
                        "inspected_after_failed_click": True,
                        "observed_url_still_results": current_url,
                        "target_url_from_observed_result": video["href"],
                        "retry_policy": "The clicked action was not repeated; navigation uses the freshly observed result URL.",
                    }
                    started = time.perf_counter()
                    recovery = await self.call("browser", {
                        "action": "navigate",
                        "url": video["href"],
                        "expected": {
                            "kind": "browser_video",
                            "url_contains": "youtube.com/watch?v=",
                            "title_contains": "Python",
                        },
                    })
                    video_passed = self.save_step(
                        "youtube_video_recovery",
                        recovery,
                        started,
                        selected_title=video["name"],
                        selected_url=video["href"],
                    )
                    observed = await self.manager.engine.observe(mode="compact")
                    video_summary = self._page_summary(observed)
                else:
                    self.steps["youtube_video_recovery"] = {
                        "executed": False,
                        "success": False,
                        "reason": "The post-click page was not the observed search results page; no recovery navigation was attempted.",
                        "observed_url": current_url,
                    }
        else:
            self.steps["youtube_video"] = {
                "executed": False,
                "success": False,
                "reason": "No visible YouTube watch link was exposed by the search results observation.",
            }
        return {
            "passed": bool(search_passed and video_passed),
            "search_page": self._page_summary(search_page),
            "video": video_summary,
        }

    async def run_amazon(self) -> dict[str, Any]:
        tab = await self.find_tab("amazon.com")
        if tab:
            started = time.perf_counter()
            result = await self.call("browser", {"action": "switch_tab", "target": tab.get("targetId") or tab.get("target_id")})
            self.save_step("amazon_tab", result, started, reused=True, tab={"id": tab.get("targetId") or tab.get("target_id"), "url": tab.get("url")})
        else:
            started = time.perf_counter()
            result = await self.call("browser", {"action": "new_tab", "url": "https://www.amazon.com"})
            self.save_step("amazon_tab", result, started, reused=False)

        current = await self.manager.engine.observe(mode="compact")
        query_values = " ".join(parse_qs(urlparse(current.get("url", "")).query).get("k", []))
        if AMAZON_QUERY not in query_values.casefold():
            url = "https://www.amazon.com/s?k=" + quote_plus(AMAZON_QUERY)
            started = time.perf_counter()
            result = await self.call("browser", {
                "action": "navigate",
                "url": url,
                "expected": {"kind": "browser_search", "query": AMAZON_QUERY},
            })
            search_passed = self.save_step("amazon_search", result, started, query=AMAZON_QUERY)
        else:
            from utils.semantic_verification import SemanticVerifier

            check = SemanticVerifier(ROOT).verify(
                {"kind": "browser_search", "query": AMAZON_QUERY},
                current,
                action_executed=True,
                state_changed=False,
            )
            self.steps["amazon_search"] = {
                "executed": False,
                "success": check.expected_state_verified,
                "expected_state_verified": check.expected_state_verified,
                "verification": check.to_dict(),
                "reused_existing_results": True,
            }
            search_passed = check.expected_state_verified

        observed = await self.manager.engine.observe(mode="detailed")
        listing_script = """(()=>[...document.querySelectorAll('[data-component-type=\\\"s-search-result\\\"]')]
          .filter(card=>{const r=card.getBoundingClientRect(),s=getComputedStyle(card);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'})
          .slice(0,8).map(card=>({
            title:(card.querySelector('h2')?.innerText||'').trim(),
            price:(card.querySelector('.a-price .a-offscreen')?.innerText||'').trim(),
            rating:(card.querySelector('[aria-label*=\\\"out of 5 stars\\\"]')?.getAttribute('aria-label')||'').trim(),
            url:card.querySelector('h2 a')?.href||''
          })).filter(item=>item.title))()"""
        try:
            raw = await self.manager.engine.execute_javascript(listing_script)
            payload = raw.get("result") if isinstance(raw, dict) else None
            listings = payload if isinstance(payload, list) else []
        except Exception as exc:
            listings = []
            self.steps["amazon_listings"] = {"executed": True, "success": False, "error": str(exc)}
        compare_passed = len(listings) >= 2
        comparison = compare_visible_listings(listings)
        self.steps.setdefault("amazon_listings", {
            "executed": True,
            "success": compare_passed,
            "count": len(listings),
            "listings": listings,
            "comparison": comparison,
            "visible_page": self._page_summary(observed),
            "comparison_basis": ["visible product title", "visible price", "visible rating"]
            if listings else [],
        })
        return {
            "passed": bool(search_passed and compare_passed),
            "search_page": self._page_summary(observed),
            "listing_count": len(listings),
            "listings": listings,
            "comparison": comparison,
        }

    async def run_tab_recovery(self) -> dict[str, Any]:
        youtube = await self.find_tab("youtube.com")
        amazon = await self.find_tab("amazon.com")
        if not youtube or not amazon:
            self.steps["multi_tab_recovery"] = {
                "executed": False,
                "success": False,
                "reason": "Both YouTube and Amazon tabs must be observable before tab recovery can be tested.",
            }
            return {"passed": False}
        yid = youtube.get("targetId") or youtube.get("target_id")
        aid = amazon.get("targetId") or amazon.get("target_id")
        initial_ids = {tab.get("targetId") or tab.get("target_id") for tab in await self.manager.engine.list_tabs()}
        start = time.perf_counter()
        yswitch = await self.call("browser", {"action": "switch_tab", "target": yid})
        ystatus = await self.manager.engine.status()
        youtube_active = ((ystatus.get("current_tab") or {}).get("targetId") or (ystatus.get("current_tab") or {}).get("target_id")) == yid
        self.save_step("switch_to_youtube", yswitch, start, active_tab_verified=youtube_active)

        start = time.perf_counter()
        aswitch = await self.call("browser", {"action": "switch_tab", "target": aid})
        astatus = await self.manager.engine.status()
        amazon_active = ((astatus.get("current_tab") or {}).get("targetId") or (astatus.get("current_tab") or {}).get("target_id")) == aid
        self.save_step("switch_to_amazon", aswitch, start, active_tab_verified=amazon_active)

        start = time.perf_counter()
        disconnected = await self.call("browser_disconnect")
        disconnected_status = await self.manager.engine.status()
        disconnected_ok = disconnected.success and disconnected_status.get("connected") is False
        self.save_step("browser_disconnect", disconnected, start, disconnected_state=disconnected_status)

        start = time.perf_counter()
        reconnected = await self.call("browser_connect")
        reconnected_status = await self.manager.engine.status()
        recovered_tabs = await self.manager.engine.list_tabs()
        recovered_ids = {tab.get("targetId") or tab.get("target_id") for tab in recovered_tabs}
        tab_recovery_ok = initial_ids.issubset(recovered_ids)
        reconnect_ok = reconnected.success and reconnected_status.get("connected") is True and tab_recovery_ok
        self.save_step(
            "browser_reconnect",
            reconnected,
            start,
            connected=reconnected_status.get("connected"),
            tab_ids_preserved=tab_recovery_ok,
            tabs=recovered_tabs,
        )
        passed = bool(youtube_active and amazon_active and disconnected_ok and reconnect_ok)
        self.steps["multi_tab_recovery"] = {
            "executed": True,
            "success": passed,
            "youtube_active_verified": youtube_active,
            "amazon_active_verified": amazon_active,
            "disconnect_verified": disconnected_ok,
            "reconnect_verified": reconnect_ok,
            "initial_tab_ids": sorted(initial_ids),
            "recovered_tab_ids": sorted(recovered_ids),
        }
        return {"passed": passed}

    async def run(self) -> dict[str, Any]:
        started_at = time.perf_counter()
        output: dict[str, Any] = {
            **self.previous,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "engine": type(self.manager.engine).__name__,
            "browser_kind": "checked-in KUBA helper daemon/CDP adapter",
            "safety": "Search and read only; no purchase, order, message, or financial action.",
        }
        try:
            connect_start = time.perf_counter()
            connected = await self.call("browser_connect")
            status = await self.manager.engine.status()
            if not connected.success or not status.get("connected"):
                raise RuntimeError(f"KUBA connection did not verify: {connected.error or status}")
            self.steps["browser_connect"] = {
                "executed": True,
                "success": True,
                "duration_seconds": round(time.perf_counter() - connect_start, 3),
                "status": status,
            }
            self.initial_tabs = await self.manager.engine.list_tabs()
            output["initial_tabs"] = self.initial_tabs

            if not (self.previous.get("google") or {}).get("passed"):
                output["google"] = await self.run_google()
            self.persist_progress("google")
            output["youtube"] = await self.run_youtube()
            self.persist_progress("youtube")
            if not (self.previous.get("amazon") or {}).get("passed"):
                output["amazon"] = await self.run_amazon()
            if not (self.previous.get("tab_recovery") or {}).get("passed"):
                output["tab_recovery"] = await self.run_tab_recovery()
            output["status"] = "passed" if all(
                bool(output.get(name, {}).get("passed")) for name in ("google", "youtube", "amazon", "tab_recovery")
            ) else "partial_or_failed"
            samples = [
                BenchmarkSample(
                    case_id="browser-navigation",
                    executed=bool(self.steps.get("google_result_open", {}).get("executed")),
                    objective_met=bool((output.get("google") or {}).get("passed")),
                    verification_correct=bool(self.steps.get("google_result_open", {}).get("expected_state_verified")),
                    duration_seconds=self.steps.get("google_result_open", {}).get("duration_seconds"),
                ),
                BenchmarkSample(
                    case_id="browser-search",
                    executed=bool(self.steps.get("youtube_search", {}).get("success") or self.steps.get("amazon_search", {}).get("success")),
                    objective_met=bool((output.get("youtube") or {}).get("passed") and (output.get("amazon") or {}).get("passed")),
                    verification_correct=bool(self.steps.get("youtube_search", {}).get("expected_state_verified") and self.steps.get("amazon_search", {}).get("expected_state_verified")),
                    duration_seconds=sum(
                        float(self.steps[key].get("duration_seconds", 0) or 0)
                        for key in ("youtube_search", "amazon_search") if key in self.steps
                    ),
                ),
                BenchmarkSample(
                    case_id="browser-multi-tab",
                    executed=bool(self.steps.get("multi_tab_recovery", {}).get("executed")),
                    objective_met=bool((output.get("tab_recovery") or {}).get("passed")),
                    verification_correct=bool(self.steps.get("multi_tab_recovery", {}).get("success")),
                    duration_seconds=sum(
                        float(self.steps[key].get("duration_seconds", 0) or 0)
                        for key in ("switch_to_youtube", "switch_to_amazon") if key in self.steps
                    ),
                ),
                BenchmarkSample(
                    case_id="browser-recovery",
                    executed=bool(self.steps.get("browser_disconnect", {}).get("executed")),
                    objective_met=bool(self.steps.get("multi_tab_recovery", {}).get("reconnect_verified")),
                    verification_correct=bool(self.steps.get("multi_tab_recovery", {}).get("reconnect_verified")),
                    recovery_attempted=True,
                    recovery_succeeded=bool(self.steps.get("multi_tab_recovery", {}).get("reconnect_verified")),
                    duration_seconds=float(self.steps.get("browser_reconnect", {}).get("duration_seconds", 0) or 0),
                ),
                BenchmarkSample(
                    case_id="browser-video",
                    executed=bool(self.steps.get("youtube_video", {}).get("executed")),
                    objective_met=bool(self.steps.get("youtube_video", {}).get("success")),
                    verification_correct=bool(self.steps.get("youtube_video", {}).get("expected_state_verified")),
                    recovery_attempted=bool(self.steps.get("youtube_video_recovery", {}).get("executed")),
                    recovery_succeeded=bool(self.steps.get("youtube_video_recovery", {}).get("success")) if self.steps.get("youtube_video_recovery", {}).get("executed") else None,
                    duration_seconds=float(self.steps.get("youtube_video", {}).get("duration_seconds", 0) or 0),
                ),
            ]
            executed_ids = {sample.case_id for sample in samples if sample.executed}
            output["evaluation"] = {
                "metrics": calculate_metrics(samples),
                "samples": [sample.to_dict() for sample in samples],
                "unexecuted_catalog_cases": [case for case in BENCHMARK_CASES if case not in executed_ids],
                "note": "Only live cases actually executed in this browser workflow are measured; other benchmark cases remain unrun.",
            }
            duplicate_keys: set[str] = set()
            repeated_side_effects = 0
            for call in self.tool_calls:
                params = call.get("params") or {}
                action = str(params.get("action", ""))
                if call.get("tool") != "browser" or action not in {
                    "click", "type", "fill", "press", "navigate", "new_tab", "close_tab"
                }:
                    continue
                key = json.dumps({"action": action, "params": params}, sort_keys=True, ensure_ascii=True)
                if key in duplicate_keys:
                    repeated_side_effects += 1
                duplicate_keys.add(key)
            output["evaluation"]["duplicate_state_changing_calls"] = repeated_side_effects
            duplicate_sample = next((sample for sample in samples if sample.executed), None)
            if duplicate_sample:
                samples[samples.index(duplicate_sample)] = BenchmarkSample(
                    **{**duplicate_sample.to_dict(), "duplicate_actions": repeated_side_effects}
                )
            output["evaluation"]["samples"] = [sample.to_dict() for sample in samples]
            output["evaluation"]["metrics"] = calculate_metrics(samples)
        except Exception as exc:
            output["status"] = "failed"
            output["fatal_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            output["tool_steps"] = self.steps
            output["tool_calls"] = self.tool_calls
            output["approval_count"] = len(self.approvals)
            output["approvals"] = self.approvals
            output["duration_seconds"] = round(time.perf_counter() - started_at, 3)
            output["context_budget_stats"] = self.context_stats
            output["final_tabs"] = (
                await self.manager.engine.list_tabs()
                if self.manager.engine and getattr(self.manager.engine, "connected", False)
                else []
            )
            await self.manager.shutdown()
        return output

    @staticmethod
    def _page_summary(page: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(page, dict):
            return None
        visible = str(page.get("visibleText", ""))
        elements = page.get("interactiveElements", [])
        return {
            "url": page.get("url"),
            "title": page.get("title"),
            "ready_state": page.get("readyState"),
            "visible_text_preview": re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted email]", visible[:500]),
            "visible_text_chars": len(visible),
            "visible_text_sha256": hashlib.sha256(visible.encode("utf-8", errors="replace")).hexdigest(),
            "observed_links": [
                {
                    "name": re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted email]", str(item.get("name", "")))[:160],
                    "href": _safe_url(str(item.get("href", ""))),
                }
                for item in elements
                if item.get("tag") == "a" and item.get("name") and item.get("href")
                and "account" not in str(item.get("name", "")).casefold()
            ][:10],
        }


def _safe_url(url: str) -> str:
    parts = urlparse(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    safe_keys = {"q", "query", "search_query", "k", "v", "list", "index"}
    cleaned = {key: values for key, values in query.items() if key in safe_keys}
    from urllib.parse import urlencode

    return parts._replace(query=urlencode(cleaned, doseq=True)).geturl()


def _sanitize_data(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(name): _sanitize_data(item, str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_data(item, key) for item in value]
    if not isinstance(value, str):
        return value
    if key in {"url", "href", "selected_url", "requested_url", "verified_url", "current_url"}:
        return _safe_url(value) if value.startswith(("http://", "https://")) else value
    if "account" in value.casefold() and re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value):
        return "[redacted account information]"
    return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[redacted email]", value)


async def main() -> int:
    workflow = LiveWorkflow()
    result = await workflow.run()
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result.get("status"),
        "engine": result.get("engine"),
        "duration_seconds": result.get("duration_seconds"),
        "google": result.get("google"),
        "youtube": result.get("youtube"),
        "amazon": result.get("amazon"),
        "tab_recovery": result.get("tab_recovery"),
        "evidence_file": str(EVIDENCE_PATH),
    }, indent=2, ensure_ascii=True))
    return 0 if result.get("status") == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
