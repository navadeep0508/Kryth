"""Task classifier — determines complexity and category before routing.

Classifies user requests into simple / medium / complex and by category
(coding, web_automation, research, data, system, content, multi) so
agent_loop.py can route to single-agent, pipeline, or parallel execution
without paying an LLM call for obvious cases.

Decision ladder:
  1. Heuristics only (O(1)) — handles ~90% of requests
  2. LLM tiebreaker (max_tokens=10) — only when heuristic score is ambiguous

Routing intent:
  simple  → run_inner_loop() directly, no planner
  medium  → planner hint injected, run_inner_loop() sequentially
  complex → run_parallel_build() if has_independent_subtasks, else medium path
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass
class TaskProfile:
    complexity: Literal["simple", "medium", "complex"]
    category: Literal["coding", "web_automation", "research", "data", "system", "content", "multi"]
    has_independent_subtasks: bool
    pipeline_type: Literal["coding", "browser", "research", "system"] | None
    reason: str


# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

_SIMPLE_STARTERS = re.compile(
    r"^(fix|debug|explain|what|why|how|show|list|print|check|review|"
    r"refactor|rename|move|delete|remove|help|tell|describe|summarize|"
    r"read|open|find|search|get|run|test|verify|format|lint|"
    r"can you|could you|please|what is|what are|how do|how to)\b",
    re.I,
)

_BUILD_VERBS = re.compile(
    r"\b(build|create|make|generate|write|develop|design|scaffold|"
    r"implement|start|setup|set up|bootstrap|init|deploy|ship|launch)\b",
    re.I,
)

# Multi-component indicators → complex, has_independent_subtasks=True
_MULTI_COMPONENT = re.compile(
    r"\b(frontend|backend|database|db|schema|auth|authentication|authorization|"
    r"payment|stripe|billing|checkout|fullstack|full.?stack|saas|platform|"
    r"microservice|api\s+and|rest\s+api|graphql|ci[/ ]?cd|devops|docker|"
    r"kubernetes|k8s|monitoring|dashboard|landing.?page|admin.?panel|"
    r"management\s+(?:system|platform|app)|event\s+(?:hub|platform|app|system)|"
    r"and\s+(?:a\s+)?(?:frontend|backend|database|api|cli|ui|auth|payment|deploy|"
    r"dashboard|admin|landing|checkout|billing))\b",
    re.I,
)

_WEB_AUTOMATION = re.compile(
    r"\b(browser|navigate|click|scrape|scraping|crawl|selenium|playwright|"
    r"form\s+fill|login|automate\s+(?:the\s+)?(?:web|browser|site)|"
    r"web\s+automation|extract\s+from\s+(?:the\s+)?(?:web|site|page)|"
    r"sign\s+in|sign\s+up|fill\s+(?:the\s+)?form|submit\s+(?:the\s+)?form|"
    r"go\s+to\s+(?:the\s+)?(?:website|site|page|url)|visit\s+(?:the\s+)?(?:website|site))\b",
    re.I,
)

# Web-navigation opener: "open X", "go to X", "navigate to X", "visit X"
_WEB_NAV_OPENER = re.compile(
    r"^(open|go\s+to|navigate\s+to|visit|launch)\s+\S",
    re.I,
)

# Common web-browsing action verbs that follow a web-nav opener
_WEB_ACTION_VERBS = re.compile(
    r"\b(type|click|search|select|fill|submit|scroll|play|pause|download|"
    r"screenshot|sign\s+in|log\s+in|register|buy|add\s+to\s+cart|checkout|"
    r"watch|open\s+(?:the\s+)?(?:link|video|page|tab)|"
    r"and\s+(?:type|click|search|select|play|watch|find|pick|choose))\b",
    re.I,
)

# Well-known websites — "open youtube …" is always web automation
_KNOWN_SITES = re.compile(
    r"\b(youtube|google|github|twitter|facebook|instagram|reddit|amazon|"
    r"netflix|spotify|linkedin|stackoverflow|wikipedia|bing|duckduckgo|"
    r"twitch|tiktok|discord|slack|notion|figma|canva|shopify|ebay)\b",
    re.I,
)

_RESEARCH_WORDS = re.compile(
    r"\b(research|investigate|compare|comparison|analyze|analyse|survey|"
    r"what\s+are\s+the\s+best|find\s+the\s+top|gather\s+information|"
    r"collect\s+data\s+on|look\s+into)\b",
    re.I,
)

_DATA_WORDS = re.compile(
    r"\b(dataset|dataframe|pandas|numpy|csv|parquet|sql\s+query|"
    r"data\s+pipeline|etl|eda|machine\s+learning|train\s+(?:a\s+)?model|"
    r"neural\s+network|deep\s+learning)\b",
    re.I,
)

_SYSTEM_WORDS = re.compile(
    r"\b(docker|dockerfile|compose|kubernetes|ansible|terraform|"
    r"shell\s+script|bash\s+script|cron\s+job|systemd|nginx|"
    r"ci[/ ]?cd|github\s+actions|pipeline\s+for\s+deploy)\b",
    re.I,
)

_CONTENT_WORDS = re.compile(
    r"\b(write\s+(?:a\s+)?(?:blog|article|essay|report|email|readme)|"
    r"draft|copywrite|summarize\s+this|translate)\b",
    re.I,
)

# Large-repo / distributed analysis indicators
_LARGE_REPO = re.compile(
    r"\b(entire\s+(?:codebase|repo|repository)|all\s+(?:modules|packages|files)|"
    r"across\s+(?:multiple|all)\s+(?:files|modules)|large\s+codebase|"
    r"audit\s+(?:the\s+)?(?:entire|whole|full))\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

def _is_web_automation(text: str) -> bool:
    """Return True if the text describes a web browser automation task."""
    # Explicit automation keywords
    if _WEB_AUTOMATION.search(text):
        return True
    # "open/go to/visit X" + at least one web action verb
    if _WEB_NAV_OPENER.match(text) and _WEB_ACTION_VERBS.search(text):
        return True
    # Mentions a known website + any action verb
    if _KNOWN_SITES.search(text) and _WEB_ACTION_VERBS.search(text):
        return True
    return False


def _detect_category(text: str) -> tuple[
    Literal["coding", "web_automation", "research", "data", "system", "content", "multi"],
    Literal["coding", "browser", "research", "system"] | None,
]:
    hits = []
    if _is_web_automation(text):
        hits.append("web_automation")
    if _RESEARCH_WORDS.search(text):
        hits.append("research")
    if _DATA_WORDS.search(text):
        hits.append("data")
    if _SYSTEM_WORDS.search(text):
        hits.append("system")
    if _CONTENT_WORDS.search(text):
        hits.append("content")

    if len(hits) >= 2:
        return "multi", None
    if len(hits) == 1:
        cat = hits[0]
        pipeline_map = {
            "web_automation": "browser",
            "research": "research",
            "system": "system",
        }
        return cat, pipeline_map.get(cat, "coding")  # type: ignore[return-value]

    return "coding", "coding"


# ---------------------------------------------------------------------------
# Heuristic scorer
# ---------------------------------------------------------------------------

def _score(text: str) -> int:
    """Return an integer complexity score.

    0–3  → simple
    4–5  → ambiguous (LLM tiebreaker)
    6+   → complex
    """
    score = 0
    words = text.split()
    word_count = len(words)

    # Word count contribution
    if word_count >= 15:
        score += 1
    if word_count >= 30:
        score += 1
    if word_count >= 50:
        score += 1

    # Build verb present
    if _BUILD_VERBS.search(text):
        score += 2

    # Multi-component keywords (strongest signal)
    if _MULTI_COMPONENT.search(text):
        score += 3

    # Large-repo / distributed analysis
    if _LARGE_REPO.search(text):
        score += 2

    return score


# ---------------------------------------------------------------------------
# LLM tiebreaker (only for ambiguous score 4–5)
# ---------------------------------------------------------------------------

_LLM_TIEBREAKER_SYSTEM = (
    "You are a task complexity classifier. "
    "Given a user request, respond with exactly one word: simple, medium, or complex.\n\n"
    "simple  = single-step, no build, no multi-component work\n"
    "medium  = one focused feature/component, sequential steps needed\n"
    "complex = multiple independent components that can be built in parallel\n\n"
    "Reply ONLY with: simple | medium | complex"
)


def _llm_tiebreaker(user_input: str) -> Literal["simple", "medium", "complex"] | None:
    try:
        from agent.llm import _get_client, PLANNER_MODEL
        client = _get_client()
        response = client.chat.completions.create(
            model=PLANNER_MODEL,
            messages=[
                {"role": "system", "content": _LLM_TIEBREAKER_SYSTEM},
                {"role": "user", "content": user_input[:400]},
            ],
            temperature=0,
            max_tokens=10,
        )
        raw = (response.choices[0].message.content or "").strip().lower()
        for level in ("simple", "medium", "complex"):
            if level in raw:
                return level  # type: ignore[return-value]
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_task(user_input: str) -> TaskProfile:
    """Classify a user request and return a TaskProfile for routing.

    Uses heuristics first. Falls back to a tiny LLM call only when the
    heuristic score is in the ambiguous 4–5 range.
    """
    text = user_input.strip()

    # Trivially empty or slash-command → simple
    if not text or text.startswith("/"):
        return TaskProfile(
            complexity="simple",
            category="coding",
            has_independent_subtasks=False,
            pipeline_type=None,
            reason="empty or slash command",
        )

    category, pipeline_type = _detect_category(text)

    words = text.split()
    score = _score(text)

    # A build prompt that mentions "login", "auth", "sign in" etc. scores high
    # because of multi-component keywords — don't let web_automation short-circuit
    # before the score check. Only treat as pure web_automation when there is NO
    # multi-component build signal (score < 6 and no build verb).
    is_web_automation = category == "web_automation"
    is_complex_build = score >= 6 or (
        _BUILD_VERBS.search(text) and _MULTI_COMPONENT.search(text)
    )

    if is_web_automation and not is_complex_build:
        return TaskProfile(
            complexity="medium",
            category="web_automation",
            has_independent_subtasks=False,
            pipeline_type="browser",
            reason="web automation detected — sequential pipeline",
        )

    # When both web_automation and complex build signals are present, treat as
    # a coding task (the build intent dominates).
    if is_web_automation and is_complex_build:
        category = "coding"
        pipeline_type = "coding"

    # Simple-starter words with no build verb → simple
    if _SIMPLE_STARTERS.match(text) and not _BUILD_VERBS.search(text):
        return TaskProfile(
            complexity="simple",
            category=category,
            has_independent_subtasks=False,
            pipeline_type=None,
            reason="simple-starter keyword, no build verb",
        )

    # Short inputs without build verb → simple
    if len(words) < 8 and not _BUILD_VERBS.search(text):
        return TaskProfile(
            complexity="simple",
            category=category,
            has_independent_subtasks=False,
            pipeline_type=None,
            reason="short input, no build verb",
        )

    # Clear simple
    if score <= 3:
        return TaskProfile(
            complexity="simple",
            category=category,
            has_independent_subtasks=False,
            pipeline_type=pipeline_type,
            reason=f"heuristic score {score} ≤ 3",
        )

    # Clear complex
    if score >= 6:
        has_independent = bool(_MULTI_COMPONENT.search(text) or _LARGE_REPO.search(text))
        return TaskProfile(
            complexity="complex",
            category=category,
            has_independent_subtasks=has_independent,
            pipeline_type=pipeline_type,
            reason=f"heuristic score {score} ≥ 6, multi-component={has_independent}",
        )

    # Ambiguous (4–5): ask LLM
    llm_level = _llm_tiebreaker(text)
    resolved: Literal["simple", "medium", "complex"] = llm_level or "medium"

    has_independent = resolved == "complex" and bool(
        _MULTI_COMPONENT.search(text) or _LARGE_REPO.search(text)
    )
    return TaskProfile(
        complexity=resolved,
        category=category,
        has_independent_subtasks=has_independent,
        pipeline_type=pipeline_type if resolved != "simple" else None,
        reason=f"heuristic score {score} (ambiguous), LLM said {llm_level or 'unavailable'} → {resolved}",
    )
