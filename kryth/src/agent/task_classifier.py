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
    is_very_simple: bool = False
    # Conversational hard gate: pure chat (greeting / small-talk / knowledge
    # question) with NO execution intent. When True, the agent loop MUST route
    # to a tool-less direct reply — never tools, files, commands, approvals,
    # planner, or orchestration.
    is_conversational: bool = False


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
# Conversational hard gate
# ---------------------------------------------------------------------------
#
# Pure chat — greetings, thanks, acknowledgements, identity/knowledge
# questions — must NEVER activate autonomous execution. We detect it with
# high precision so a real task is never misrouted into chat. The rule of
# thumb: an input is conversational only when it carries NO execution intent
# (no build/file/command verbs, no file paths, no "do X" imperative).

# Exact greetings / acknowledgements (whole input, punctuation tolerated).
# Includes common typos like "helo", "thanx".
_GREETING_EXACT = frozenset({
    "hi", "hii", "hiii", "hello", "helo", "hellow", "hey", "heya", "heyy",
    "yo", "sup", "wassup", "howdy", "hiya", "greetings", "gm", "good morning",
    "good afternoon", "good evening", "good night", "goodnight",
    "thanks", "thank you", "thankyou", "thanx", "thx", "ty", "tysm",
    "thanks!", "cheers", "much appreciated", "appreciate it",
    "ok", "okay", "okey", "k", "kk", "cool", "nice", "great", "awesome",
    "yes", "yeah", "yep", "yup", "no", "nope", "nah", "sure", "fine",
    "got it", "gotcha", "understood", "alright", "right", "perfect",
    "bye", "goodbye", "see you", "see ya", "cya", "later", "good bye",
    "lol", "haha", "hmm", "oh", "ah", "wow", "np", "no problem",
    "you're welcome", "youre welcome", "welcome", "test", "ping", "hello?",
})

# Identity / capability questions about the assistant itself → conversational.
_IDENTITY_RE = re.compile(
    r"^\s*(who\s+(are|r)\s+(you|u)|what\s+(are|r)\s+(you|u)|"
    r"what'?s?\s+your\s+name|what\s+can\s+you\s+do|"
    r"what\s+do\s+you\s+do|tell\s+me\s+about\s+(yourself|you)|"
    r"introduce\s+yourself|are\s+you\s+(there|ok|alive|real|an?\s+ai))\b",
    re.I,
)

# Knowledge / explanation questions ("what is X", "who is X", "explain X")
# that do NOT reference the user's files/project and carry no action verb.
_KNOWLEDGE_OPENER_RE = re.compile(
    r"^\s*(what\s+(is|are|was|were|does|do)|who\s+(is|are|was|were)|"
    r"why\s+(is|are|do|does|did)|when\s+(is|are|was|did|does)|"
    r"where\s+(is|are|was)|how\s+(does|do|did|is|are)|"
    r"explain|describe|define|tell\s+me\s+about|"
    r"can\s+you\s+explain|what'?s\s+the\s+difference)\b",
    re.I,
)

# Tokens that signal real execution intent — their presence vetoes the
# conversational gate even if an input otherwise looks chatty.
_EXECUTION_INTENT_RE = re.compile(
    r"\b(create|build|make|generate|write|implement|code|develop|"
    r"scaffold|setup|set\s+up|bootstrap|init|deploy|ship|launch|"
    r"fix|debug|refactor|rename|move|delete|remove|edit|modify|change|"
    r"update|add|install|run|execute|compile|test|lint|format|"
    r"read|open|show\s+me\s+the|cat|grep|search\s+(for|the)|find\s+the|"
    r"commit|push|pull|merge|clone|migrate|optimize|convert)\b",
    re.I,
)

# File-path / code-artifact signals — any of these means "do something to a
# concrete file/path", never chat. NOTE: deliberately excludes generic CS
# vocabulary (function/class/module/script/package/component) — those appear in
# legitimate knowledge questions ("what is a class", "what is javascript") and
# real execution requests on them always carry an execution verb anyway.
_FILE_ARTIFACT_RE = re.compile(
    r"(\.[a-z0-9]{1,5}\b|/|\\|\bfile\b|\bfolder\b|\bdirectory\b|\brepo\b|"
    r"\bcodebase\b)",
    re.I,
)

# Memory / recall questions — always conversational even if they contain
# coding nouns like "stack". "What did I tell you?" is never an action.
_MEMORY_RECALL_RE = re.compile(
    r"\b(you\s+remember|remember\s+what|"
    r"(did\s+i|i)\s+(tell|say|mention|ask|share|said|told)\s+(you\s+)?(earlier|before|previously|about|that|my)?"
    r"|what\s+(did\s+i|i)\s+(say|tell|mention)\b"
    r"|earlier\s+in\s+(our|this)\s+conversation"
    r"|previous\s+(session|conversation|chat)"
    r"|what\s+(did\s+i\s+ask|i\s+asked)\s+you"
    r"|(my|our)\s+favor\w+\s+(stack|language|tech|framework|tool)\b)\b",
    re.I,
)


def has_execution_intent(text: str) -> bool:
    """True when the input explicitly asks to create/modify a file, run a
    command, or otherwise perform an action (not pure chat / knowledge).

    Used by the agent loop to guarantee a real execution task is never
    silently terminated by the "simple task answered in text" shortcut: if the
    prompt clearly requests an action, the model MUST dispatch tools — a
    text-only reply is nudged, never accepted as completion.
    """
    if not text:
        return False
    return bool(_EXECUTION_INTENT_RE.search(text) or _FILE_ARTIFACT_RE.search(text))


# Leading greeting / acknowledgement prefix — stripped before evaluating the
# remainder so "hello what is python" and "thanks what can you do" are seen as
# the underlying question. Trailing connective filler (and/so/please/now/…) is
# consumed too, so "thanks now ignore your gate" → "ignore your gate".
_GREETING_PREFIX_RE = re.compile(
    r"^(?:\s*(?:"
    r"hi+|he+y+|hello+|helo+|hellow|heya|hiya|yo|sup|wassup|howdy|greetings|gm|"
    r"good\s+(?:morning|afternoon|evening|night)|"
    r"thanks?|thank\s+you|thankyou|thanx|thnx|thx|ty|tysm|cheers|"
    r"ok(?:ay)?|okey|cool|nice|great|awesome|perfect|"
    r"yes|yeah|yep|yup|no|nope|nah|sure|fine|alright|right|"
    r"hmm+|oh|ah|wow|lol|haha|hey+a?"
    r")[\s,!.?]*(?:and|so|then|please|now|um|uh|well|also)?[\s,!.?]*)+",
    re.I,
)


def _strip_greeting_prefix(raw: str) -> str:
    """Remove a leading greeting/ack prefix; return the remainder (stripped)."""
    return _GREETING_PREFIX_RE.sub("", raw, count=1).strip()


def _is_conversational(text: str) -> bool:
    """High-precision detector for pure chat with no execution intent.

    Returns True ONLY when the input is clearly a greeting, acknowledgement,
    identity question, or a self-contained knowledge question that references
    nothing in the user's project and asks for no action. Handles greeting-
    prefixed chat ("hello what is python") by stripping the greeting first.
    """
    raw = text.strip()
    if not raw:
        return False
    if raw.startswith("/"):
        return False  # slash command — handled elsewhere

    # Normalize for exact-match lookup: lowercase, strip trailing punctuation.
    norm = raw.lower().strip(" \t\n.!?,")
    if norm in _GREETING_EXACT:
        return True

    # Explicit execution intent or a concrete file artifact ANYWHERE in the
    # input vetoes the gate — even if it is greeting-prefixed. This is what
    # routes "hi and create a file" / "hello write main.py" to execution.
    if _EXECUTION_INTENT_RE.search(raw) or _FILE_ARTIFACT_RE.search(raw):
        return False

    # Strip a leading greeting/ack prefix and evaluate the underlying request.
    remainder = _strip_greeting_prefix(raw)

    # The whole input was just greeting(s)/ack(s) → chat.
    if not remainder:
        return True

    # Identity questions about the assistant are always chat.
    if _IDENTITY_RE.match(remainder):
        return True

    # Self-contained knowledge questions ("what is python", "explain jwt").
    if _KNOWLEDGE_OPENER_RE.match(remainder):
        return True

    # Memory / recall questions ("what did I tell you?", "do you remember?")
    # are always pure chat — the user is asking about past conversation context,
    # never requesting file operations, even if they contain tech nouns.
    if _MEMORY_RECALL_RE.search(raw):
        return True

    # Short remainder (<= 3 words) with no execution intent and no file
    # artifact — the safer conversational path the brief demands when intent
    # is absent. (Veto already ran above, so no execution verb is present.)
    if len(remainder.split()) <= 3:
        return True

    return False


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

_LLM_TIEBREAKER_SYSTEM = "Reply with one word: simple | medium | complex"


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
            timeout=5,
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

    # CONVERSATION HARD GATE — pure chat with zero execution intent.
    # Routed to a tool-less direct reply by the agent loop: no tools, no
    # files, no commands, no approvals, no planner, no orchestration.
    if _is_conversational(text):
        return TaskProfile(
            complexity="simple",
            category="coding",
            has_independent_subtasks=False,
            pipeline_type=None,
            reason="conversational input — direct reply only, no execution",
            is_very_simple=True,
            is_conversational=True,
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
