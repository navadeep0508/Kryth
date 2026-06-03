"""Self-healing selector system for robust element finding.

Recovery chain (never fails immediately):
    CSS selector -> text search -> role/aria -> XPath -> vision (screenshot) -> error

Each strategy tries to find the element; if it fails, the next strategy
in the chain is attempted. Only after all strategies have been exhausted
does the system report failure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from playwright.sync_api import ElementHandle, Page

logger = logging.getLogger(__name__)


@dataclass
class SelectorResult:
    """Result of a selector strategy attempt."""
    element: ElementHandle | None = None
    strategy: str = ""
    confidence: float = 0.0  # 0.0 - 1.0
    error: str = ""


StrategyFn = Callable[[Page, str], SelectorResult]


class SelectorEngine:
    """Self-healing selector engine with fallback strategy chain.

    Usage:
        engine = SelectorEngine(page)
        result = engine.find_element("#submit-button")
        if result.element:
            result.element.click()
    """

    def __init__(self, page: Page, vision_fallback: Optional[Callable] = None) -> None:
        self._page = page
        self._vision_fallback = vision_fallback
        self._strategies: list[tuple[str, StrategyFn]] = [
            ("css", self._try_css),
            ("css_alt", self._try_css_alt),
            ("text", self._try_text),
            ("role", self._try_role),
            ("xpath", self._try_xpath),
            ("placeholder", self._try_placeholder),
            ("label", self._try_label),
            ("partial_text", self._try_partial_text),
            ("nearby_text", self._try_nearby_text),
        ]

    def find_element(
        self,
        selector: str,
        *,
        timeout: float = 5000,
        retries: int = 3,
    ) -> SelectorResult:
        """Find an element using the full recovery chain.

        Tries each strategy in order. Returns the first successful result.
        If all strategies fail, returns a result with element=None and
        the accumulated error messages.

        Args:
            selector: The CSS selector or text descriptor to search for.
            timeout: Per-strategy timeout in milliseconds.
            retries: Number of times to retry the full chain before failing.

        Returns:
            A SelectorResult with the found element (or None).
        """
        last_result = SelectorResult(error="No strategies attempted")

        for attempt in range(retries):
            for strategy_name, strategy_fn in self._strategies:
                try:
                    result = strategy_fn(self._page, selector)
                except Exception as e:
                    result = SelectorResult(
                        element=None,
                        strategy=strategy_name,
                        error=f"{type(e).__name__}: {e}",
                    )

                if result.element:
                    logger.debug(
                        "Found element with strategy '%s' (attempt %d/%d)",
                        strategy_name, attempt + 1, retries,
                    )
                    return result

                last_result = result

            # Brief pause before retrying the full chain
            if attempt < retries - 1:
                import time
                time.sleep(0.3)

        # ---- Vision fallback (last resort) ----
        if self._vision_fallback:
            try:
                vision_result = self._vision_fallback(self._page, selector, "click")
                if vision_result and vision_result.element:
                    return vision_result
            except Exception as e:
                last_result = SelectorResult(
                    element=None,
                    strategy="vision",
                    error=f"Vision fallback failed: {e}",
                )

        return SelectorResult(
            element=None,
            strategy="all_exhausted",
            error=f"Element not found after {retries} attempts using all strategies. "
                  f"Last strategy '{last_result.strategy}': {last_result.error}",
        )

    def find_elements(
        self,
        selector: str,
        *,
        timeout: float = 5000,
    ) -> list[ElementHandle]:
        """Find all elements matching a selector, with fallback.

        Tries strategies and returns all matching elements from the
        first strategy that returns any results.
        """
        for strategy_name, strategy_fn in self._strategies:
            try:
                result = strategy_fn(self._page, selector)
            except Exception:
                continue
            if result.element:
                # Found at least one; try to get all matching elements
                try:
                    elements = self._page.query_selector_all(selector)
                    if elements:
                        return elements
                except Exception:
                    pass
                return [result.element]
        return []

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    def _try_css(self, page: Page, selector: str) -> SelectorResult:
        """Primary CSS selector strategy."""
        try:
            el = page.wait_for_selector(selector, timeout=2000)
            if el:
                return SelectorResult(element=el, strategy="css", confidence=0.95)
        except Exception as e:
            pass
        return SelectorResult(error="css selector not found")

    def _try_css_alt(self, page: Page, selector: str) -> SelectorResult:
        """Alternative CSS — try common transformations.

        Handles cases where the original selector may have a different
        prefix or suffix than expected.
        """
        transformations = [
            selector,
            f"[data-testid='{selector.replace('.', '').replace('#', '')}']",
            f"[id*='{_strip_prefixes(selector)}']",
            f"[class*='{_strip_prefixes(selector)}']",
            f"[name='{_strip_prefixes(selector)}']",
        ]
        for tf in transformations:
            try:
                el = page.wait_for_selector(tf, timeout=500)
                if el:
                    return SelectorResult(
                        element=el, strategy="css_alt", confidence=0.7,
                    )
            except Exception:
                continue
        return SelectorResult(error="css_alt not found")

    def _try_text(self, page: Page, selector: str) -> SelectorResult:
        """Find by exact visible text using Playwright's text selector."""
        text_query = selector
        # If the original selector looks like CSS, use its text content
        # as the search target instead.
        if _looks_like_css(selector):
            return SelectorResult(error="looks like css, skipping text")

        try:
            el = page.get_by_text(text_query, exact=True).first.element_handle()
            if el:
                return SelectorResult(element=el, strategy="text", confidence=0.85)
        except Exception:
            pass
        return SelectorResult(error="exact text not found")

    def _try_partial_text(self, page: Page, selector: str) -> SelectorResult:
        """Find by partial visible text match."""
        if _looks_like_css(selector):
            return SelectorResult(error="looks like css, skipping partial_text")

        try:
            el = page.get_by_text(selector, exact=False).first.element_handle()
            if el:
                return SelectorResult(
                    element=el, strategy="partial_text", confidence=0.75,
                )
        except Exception:
            pass
        return SelectorResult(error="partial text not found")

    def _try_role(self, page: Page, selector: str) -> SelectorResult:
        """Find by ARIA role or accessibility attribute."""
        role = _detect_role(selector)
        if role:
            try:
                name = _extract_name_from_selector(selector)
                locator = page.get_by_role(role, name=name) if name else page.get_by_role(role)
                el = locator.first.element_handle()
                if el:
                    return SelectorResult(
                        element=el, strategy="role", confidence=0.8,
                    )
            except Exception:
                pass

        # Try generic ARIA attributes
        try:
            el = page.wait_for_selector(
                f"[aria-label*='{_strip_prefixes(selector)}']",
                timeout=500,
            )
            if el:
                return SelectorResult(
                    element=el, strategy="role", confidence=0.7,
                )
        except Exception:
            pass
        return SelectorResult(error="role/aria not found")

    def _try_xpath(self, page: Page, selector: str) -> SelectorResult:
        """Find by XPath — try various XPath constructions."""
        xpath_candidates = [
            f"//*[contains(text(), '{_escape_xpath(selector)}')]",
            f"//*[@value='{_escape_xpath(selector)}']",
            f"//*[@placeholder='{_escape_xpath(selector)}']",
        ]
        for xpath in xpath_candidates:
            try:
                el = page.wait_for_selector(f"xpath={xpath}", timeout=500)
                if el:
                    return SelectorResult(
                        element=el, strategy="xpath", confidence=0.65,
                    )
            except Exception:
                continue
        return SelectorResult(error="xpath not found")

    def _try_placeholder(self, page: Page, selector: str) -> SelectorResult:
        """Find input elements by placeholder attribute."""
        name = _strip_prefixes(selector)
        try:
            el = page.wait_for_selector(
                f"input[placeholder*='{name}'], textarea[placeholder*='{name}']",
                timeout=500,
            )
            if el:
                return SelectorResult(
                    element=el, strategy="placeholder", confidence=0.75,
                )
        except Exception:
            pass
        return SelectorResult(error="placeholder not found")

    def _try_label(self, page: Page, selector: str) -> SelectorResult:
        """Find elements by their associated label text."""
        name = _strip_prefixes(selector)
        try:
            label = page.wait_for_selector(
                f"label:has-text('{name}')",
                timeout=500,
            )
            if label:
                for_id = label.get_attribute("for")
                if for_id:
                    el = page.wait_for_selector(f"#{for_id}", timeout=500)
                    if el:
                        return SelectorResult(
                            element=el, strategy="label", confidence=0.8,
                        )
        except Exception:
            pass
        return SelectorResult(error="label not found")

    def _try_nearby_text(self, page: Page, selector: str) -> SelectorResult:
        """Find elements near text that matches.

        Scans the page for visible text containing the selector string,
        then tries clicking nearby interactive elements.
        """
        if _looks_like_css(selector):
            return SelectorResult(error="looks like css, skipping nearby_text")
        try:
            elements = page.query_selector_all(
                "button, a, input, select, textarea, [role='button'], [role='link']"
            )
            found_text = _strip_prefixes(selector).lower()
            for el in elements:
                text = (el.inner_text() or "").lower()
                val = el.get_attribute("value") or ""
                if found_text in text or found_text in val:
                    return SelectorResult(
                        element=el, strategy="nearby_text", confidence=0.6,
                    )
        except Exception:
            pass
        return SelectorResult(error="nearby_text not found")


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

_CSS_PREFIX_RE = re.compile(r'^[.#\[]+')


def _strip_prefixes(selector: str) -> str:
    """Remove CSS-like prefixes (.class, #id) for text-based strategies."""
    s = selector.strip()
    s = _CSS_PREFIX_RE.sub('', s)
    return s.split(' ')[0] if ' ' in s else s


def _looks_like_css(selector: str) -> bool:
    """Heuristic: True if the selector starts with . # or looks like a tag."""
    s = selector.strip()
    return bool(
        s.startswith(('.', '#', '['))
        or s.startswith(('input', 'button', 'a', 'div', 'span', 'select', 'textarea'))
    )


_ARIA_ROLES = {
    "button": "button",
    "link": "link",
    "checkbox": "checkbox",
    "radio": "radio",
    "dropdown": "combobox",
    "menu": "menu",
    "tab": "tab",
    "search": "searchbox",
    "input": "textbox",
    "textbox": "textbox",
    "heading": "heading",
    "image": "img",
    "list": "list",
    "listitem": "listitem",
    "table": "table",
    "dialog": "dialog",
    "alert": "alert",
    "banner": "banner",
    "navigation": "navigation",
    "main": "main",
    "complementary": "complementary",
    "form": "form",
}


def _detect_role(selector: str) -> str | None:
    """Try to detect an ARIA role from a selector string."""
    s = selector.strip().lower()
    for key, role in _ARIA_ROLES.items():
        if s.startswith(key) or s == key or s.endswith(key):
            return role
    return None


def _extract_name_from_selector(selector: str) -> str:
    """Extract a human-readable name from a selector for role-based queries."""
    # Remove common prefixes
    for prefix in ["button", "link", "checkbox", "the", "click", "type in"]:
        if selector.lower().startswith(prefix):
            selector = selector[len(prefix):].strip()
            break
    return selector.strip(' "\'')


def _escape_xpath(value: str) -> str:
    """Escape a string for safe embedding in XPath."""
    return value.replace("'", "&apos;").replace('"', "&quot;")
