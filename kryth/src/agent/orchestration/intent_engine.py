"""Intent Engine — classifies user requests into one or more intents.

Supports compound intents (e.g. "fix the auth bug and add payment support"
is both BugFix and Feature). Never uses a single fixed category.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Literal


IntentType = Literal[
    "bug_fix", "feature", "refactor", "migration",
    "optimization", "deployment", "architecture",
    "security", "research", "debugging", "infrastructure",
    "testing", "documentation", "support",
]


@dataclass
class Intent:
    type: IntentType
    confidence: float          # 0.0 – 1.0
    evidence: List[str]        # keywords that triggered this
    scope: str                 # "file" | "component" | "system"


@dataclass
class IntentProfile:
    intents: List[Intent]
    primary: IntentType
    is_compound: bool
    requires_repo_analysis: bool   # True when intent needs to know what's in repo
    requires_planning: bool        # True when multi-step planning is worthwhile
    raw: str


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------

_INTENT_PATTERNS: List[tuple[IntentType, str, float]] = [
    ("bug_fix",        r"\b(fix|bug|broken|crash|error|exception|fail|"
                       r"not\s+work|doesn't\s+work|wrong\s+output|"
                       r"regression|issue|problem|unexpected)\b", 0.9),
    ("debugging",      r"\b(debug|trace|log|inspect|why\s+is|what\s+causes|"
                       r"print\s+statement|breakpoint|stacktrace|diagnose)\b", 0.85),
    ("feature",        r"\b(add|implement|build|create|new\s+feature|"
                       r"support\s+for|integrate|enable|allow|support)\b", 0.8),
    ("refactor",       r"\b(refactor|clean\s*up|reorganize|restructure|"
                       r"simplify|extract|consolidate|dry\s*up|"
                       r"separate\s+concerns|improve\s+code)\b", 0.85),
    ("migration",      r"\b(migrat|upgrade\s+(?:from|to)|move\s+(?:from|to)|"
                       r"switch\s+(?:from|to)|convert\s+(?:from|to)|"
                       r"replace\s+\w+\s+with)\b", 0.9),
    ("optimization",   r"\b(optim|speed\s*up|slow|performance|latency|"
                       r"memory\s+leak|profil|cache|faster|reduce\s+(?:time|cost|size))\b", 0.85),
    ("deployment",     r"\b(deploy|ship|release|publish|production|"
                       r"ci[/\s]?cd|pipeline|workflow|github\s+actions|"
                       r"docker|kubernetes|k8s|heroku|vercel|fly\.io)\b", 0.8),
    ("architecture",   r"\b(architect|design\s+(?:the\s+)?system|"
                       r"overall\s+structure|redesign|microservice|"
                       r"monolith|event.driven|domain.driven|ddd|"
                       r"cqrs|event\s+sourcing|hexagonal)\b", 0.9),
    ("security",       r"\b(secur|vulnerabil|cve|exploit|"
                       r"sql\s+injection|xss|csrf|auth(?:orization)?\s+check|"
                       r"pentest|sanitize|encrypt|audit)\b", 0.9),
    ("research",       r"\b(research|investigate|compare|survey|"
                       r"which\s+(?:is|are)\s+best|pros\s+and\s+cons|"
                       r"evaluate|recommend|should\s+(?:I|we)\s+use)\b", 0.75),
    ("infrastructure", r"\b(infrastructure|iac|terraform|ansible|"
                       r"server\s+setup|cloud|aws|gcp|azure|"
                       r"networking|load\s+balanc|ssl|tls|dns)\b", 0.85),
    ("testing",        r"\b(test|spec|coverage|unit\s+test|integration\s+test|"
                       r"e2e|end.to.end|mock|stub|fixture|assert|"
                       r"tdd|bdd|pytest|jest|vitest)\b", 0.8),
    ("documentation",  r"\b(doc|readme|comment|docstring|wiki|"
                       r"api\s+doc|swagger|openapi|changelog|"
                       r"explain\s+(?:the\s+)?code|write\s+docs)\b", 0.75),
    ("support",        r"\b(how\s+(?:do\s+)?(?:I|we)|help\s+(?:me|us)|"
                       r"explain|what\s+is|how\s+(?:does|should))\b", 0.6),
]

_SCOPE_PATTERNS: List[tuple[str, str]] = [
    ("system",    r"\b(entire|whole|all|system|codebase|architecture|platform)\b"),
    ("component", r"\b(module|service|component|feature|endpoint|page|function|class)\b"),
    ("file",      r"\b(file|line|method|field|variable|import)\b"),
]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze_intent(user_input: str) -> IntentProfile:
    """Classify the user request and return an IntentProfile."""
    text = user_input.strip()
    text_lower = text.lower()

    found: List[Intent] = []

    for intent_type, pattern, base_conf in _INTENT_PATTERNS:
        matches = re.findall(pattern, text_lower, re.I)
        if matches:
            # Boost confidence when multiple distinct keywords match
            conf = min(1.0, base_conf + 0.05 * (len(matches) - 1))
            evidence = list(dict.fromkeys(
                m if isinstance(m, str) else m[0] for m in matches
            ))[:5]
            scope = _detect_scope(text_lower)
            found.append(Intent(
                type=intent_type,
                confidence=conf,
                evidence=evidence,
                scope=scope,
            ))

    # Sort by confidence descending
    found.sort(key=lambda i: -i.confidence)

    # Deduplicate: if bug_fix and debugging both found, keep both but mark compound
    primary: IntentType = found[0].type if found else "feature"
    is_compound = len(found) >= 2

    # Determine if we need repo analysis / planning
    requires_repo = primary in (
        "feature", "refactor", "migration", "architecture",
        "optimization", "security", "deployment",
    )
    requires_planning = primary in (
        "feature", "refactor", "migration", "architecture",
        "deployment", "infrastructure",
    ) or is_compound

    return IntentProfile(
        intents=found[:6],  # cap at 6
        primary=primary,
        is_compound=is_compound,
        requires_repo_analysis=requires_repo,
        requires_planning=requires_planning,
        raw=text,
    )


def _detect_scope(text: str) -> str:
    for scope, pattern in _SCOPE_PATTERNS:
        if re.search(pattern, text, re.I):
            return scope
    return "component"
